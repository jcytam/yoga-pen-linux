# Diagnosing it

How the pen bug was tracked down, with the commands and the evidence they produced.
Useful if you have a similar pad/pen combo, or if a kernel update changes things.

## 1. Is the pen even there?

```sh
grep -A4 -i wacf /proc/bus/input/devices
readlink /sys/bus/i2c/devices/i2c-WACF2205:00/0018:04F3:3355.0002/driver
sudo dmesg | grep -iE 'WACF|04F3'
```

Three input devices are created at boot by **hid-generic** (Mouse, Touchpad, and
an unnamed third one — the pen). A fraction of a second later **hid-multitouch**
takes the device over and creates **only** the Touchpad node. The unnamed node,
which is the pen, is never recreated.

That asymmetry is the whole bug in one line of dmesg.

## 2. Does the hardware report the pen?

Dump the report descriptor:

```sh
sudo cat /sys/kernel/debug/hid/0018:04F3:3355.0002/rdesc
```

It contains two application collections:

```
05 0d 09 05 a1 01 85 52   # Touch Pad  -> report ID 0x52 (the touchpad)
...
05 0d 09 01 a1 01 85 12   # Digitizer  -> report ID 0x12 (the pen)
```

The pen collection (Stylus, `09 20 a1 00`) declares Tip Switch (`0x42`), Barrel
Switch (`0x44`), Eraser (`0x45`), Invert (`0x3c`), In Range (`0x32`), X/Y,
Tip Pressure (`0x30`, logical max 4096), and Tilt X/Y (`0x3d`/`0x3e`, ±9000).

Then read the raw node while the pen is hovered and pressed:

```sh
sudo chmod 666 /dev/hidraw1        # or match the uaccess rule instead
python3 - <<'PY'
import os, collections
fd = os.open('/dev/hidraw1', os.O_RDONLY | os.O_NONBLOCK)
c = collections.Counter()
for _ in range(200000):
    try: d = os.read(fd, 4096)
    except BlockingIOError: continue
    if d: c[d[0]] += 1
print({hex(k): v for k, v in c.items()})
PY
```

On this machine a few seconds of hovering and drawing gives ~770 reports with
report ID `0x12`. Byte 1 holds the flags: bit 0 = tip, bit 1 = barrel,
bit 5 = in range. Pressure (bytes 6-7, 0..4096) tracked the actual pen pressure
up to ~1800. So: hardware fine, HID fine, mapping missing.

## 3. Prove hid-multitouch is the culprit

```sh
sudo modprobe -r hid_multitouch
grep -A8 '04F3:3355' /proc/bus/input/devices
```

Now a node appears with `BTN_TOOL_PEN`, `BTN_STYLUS`, `ABS_X` 0..9100,
`ABS_Y` 0..5720, `ABS_PRESSURE` 0..4096 and `ABS_TILT_X/Y` — exactly what
`hid-input` builds from the Digitizer collection. Reload the module
(`sudo modprobe hid_multitouch`) and it vanishes again.

Conclusion: `hid-multitouch` returns from its input mapping for every usage in a
collection that is neither `HID_DG_TOUCHSCREEN` nor `HID_DG_TOUCHPAD`, so the
pen collection gets swallowed and hid-input never gets to map it.

## 4. The fix: replay through uhid instead of patching the kernel

Rather than carrying a patched `hid-multitouch` (which needs the exact kernel
source and breaks on every update), copy the pen collection verbatim out of the
report descriptor, drop the vendor Feature report (`b1 02`, report ID `0x11`) so
nothing has to answer `GET_REPORT`, and create a uhid device from it. Then
forward every hidraw report with ID `0x12` with `UHID_INPUT2`.

The kernel maps it with the same code path that works under hid-generic, so you
get `BTN_TOOL_PEN`, `BTN_TOOL_RUBBER`, `BTN_STYLUS`, pressure and tilt, and udev
tags the node `ID_INPUT_TABLET` because `BTN_STYLUS` is present.

Two gotchas:

- Find the hidraw node by matching **both** `HID_ID` and `HID_PHYS`
  (`/sys/class/hidraw/*/device/uevent`). Vendor/product alone also matches the
  virtual device the bridge creates, and you get an echo loop.
- The in-range bit doubles as the "should I suppress the touchpad" signal:
  `EVIOCGRAB` the Touchpad event node while it is set, release it ~0.7 s after it
  clears. The kernel drops the grab automatically if the bridge dies.

## Button and tilt encoding

Report `0x12` is 20 bytes, in this order:

```
12  <flags>  X(2)  Y(2)  pressure(2)  vendor(2)  serial(4)  vendor(1)  vendor(1)  tiltX(2)  tiltY(2)
```

The 8-bit field at byte 15 is declared as `Battery Strength` (`0x0D/0x3B`) in the
vendor descriptor, but the firmware only ever sends `0x00` (pen out of range) or
`0xff` (in range) — it is a presence flag, not a charge level. The bridge re-tags
that usage as a vendor usage (same 8 bits, so this layout is unchanged) so
hid-input does not register a fake battery; otherwise every bridge instance adds
a `power_supply` that shows up in UPower and KDE's Power & Battery panel, reading
100% while the pen hovers and 0% the moment it is put down.

`<flags>` (byte 1), straight from the descriptor:

| bit | usage | seen on this pad |
|---|---|---|
| 0 | Tip Switch | `0x01` when the tip is down |
| 1 | Barrel Switch | `0x02` — the **upper** button (`BTN_STYLUS`) |
| 2 | Eraser | never set |
| 3 | Invert | `0x08` — the button **nearest the tip** (`BTN_TOOL_RUBBER`) |
| 4 | vendor `0x0D/0x5A` | never set |
| 5 | In Range | `0x20` |
| 6-7 | vendor — `0xff00/0x44`, `0xff00/0x5a` | `0x40` / `0x80`, set alongside the matching button |

Observed state changes while hovering (~2,800 reports, both buttons pressed
separately):

```
0x20                  in range, nothing pressed
0x40 -> 0x68          button nearest the tip  (0x40 | Invert | InRange)
0xa2                  upper button            (Barrel | InRange | 0x80)
0xa3                  upper button + tip down
0x00                  out of range
```

There is no eraser tip on this pen, so the eraser people see in drawing apps is
that near-tip button: `Invert` maps to `BTN_TOOL_RUBBER` in hid-input.

Tilt is declared (`0x0D/0x3D`, `0x0D/0x3E`, 16-bit, ±9000, bytes 16-19) but the
firmware never populates it: `TiltX` and `TiltY` were `0` in all 4,155 reports
of a test that tilted the pen ±45° on both axes, ~70° from vertical, and then
wrote with a normal grip. The axes exist in the report and in evdev; the values
are always zero.

## See also

Upstream bug report: [kernel Bugzilla #222273 — "[HID] Elan/Wacom 04F3:3355
haptic touchpad / pen hybrid: Touchpad works, but pen digitizer collection is
dropped"](https://bugzilla.kernel.org/show_bug.cgi?id=222273) (status NEW). The
step-3 experiment above — unload `hid_multitouch`, watch the pen appear — is the
cleanest reproducer to attach to that report.
