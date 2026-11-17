# yoga-pen-linux

Makes the **Lenovo Yoga Pen 2** work on Linux on the Yoga Pro 7 15ASH11, whose
haptic Force Pad doubles as the pen digitizer.

Tested on CachyOS, kernel `7.3.4-1-cachyos`, KDE Plasma (Wayland). Hardware:
ACPI `WACF2205`, ELAN i2c-HID `04F3:3355`.

## The problem

The pen produces no input at all. The Force Pad and the pen are *one* i2c-HID
device: the touchpad is the "Touch Pad" application collection (report ID
`0x52`) and the pen is a separate "Digitizer" collection (report ID `0x12`, with
tip switch, in-range, tip pressure and tilt). `hid-multitouch` claims the device
for the touchpad and never exports the Digitizer collection, so the kernel
creates **no input node for the pen**: no `BTN_TOOL_PEN`, no pressure, nothing
for libinput, Krita, Xournal++ or anything else to see.

The pen hardware is fine — it reports correctly at the HID level, and if you
unload `hid_multitouch` a working `Pen` device appears. It is a driver mapping
bug, not a broken pen or a flat battery.

## What this repo does

| Piece | Purpose |
|---|---|
| `src/yoga-pen-bridge.py` | Replays the pen's HID collection through `/dev/uhid` so the kernel's own hid-input builds the tablet (tip, pressure, tilt, eraser). |
| `systemd/yoga-pen-bridge.service` | User service; starts at login, restarts on failure. |
| `udev/70-yoga-pen.rules` | `TAG+="uaccess"` on the hidraw and touchpad nodes, so the bridge runs unprivileged. |

The bridge also suppresses the touchpad while the pen is in range, so a resting
hand cannot move the pointer while you write. That is pen behaviour, not touchpad
tuning — pass `--no-grab` to turn it off (see below).

## Install

```sh
git clone https://github.com/jcytam/yoga-pen-linux
cd yoga-pen-linux
./install.sh
```

The pen works immediately, no logout needed. Uninstall with `./uninstall.sh`.

## Verify

```sh
# libinput sees a pen
libinput list-devices | grep -A5 '3355 Pen'   # -> Capabilities: tablet

# the kernel built the tablet node
grep -A2 '3355 Pen' /proc/bus/input/devices   # -> BTN_TOOL_PEN, BTN_TOUCH, ABS_PRESSURE

# watch the bridge
journalctl --user -u yoga-pen-bridge -f
```

## Options

Run the bridge by hand with:

```sh
yoga-pen-bridge.py --no-grab     # never touch the touchpad
yoga-pen-bridge.py --verbose     # log every forwarded report
```

To make a flag permanent, add it to `ExecStart=` in
`~/.config/systemd/user/yoga-pen-bridge.service`.

## Caveats

- **This is a userspace workaround.** The real fix belongs in `hid-multitouch`,
  which should let non-touchpad application collections fall through to
  hid-input (upstream already does this for some devices). See
  [Upstream](#upstream). If a future kernel fixes it, you will see a duplicate
  pen device — disable this service.
- **The barrel/side button does nothing.** This pad's pen reports no barrel-switch
  bit in any HID field; measured across thousands of reports while clicking it.
  There is nothing to map. Tip and eraser work.
- **Scope: the pen only.** Touchpad, palm and gesture behaviour are untouched by
  this repo. While the pen is in range the touchpad is suppressed; that is the
  only interaction with it.
- The pen maps the **pad area to the whole screen** (absolute pointer), i.e. it
  behaves like an absolute tablet for the pad, not like a drawing tablet on the
  display. The touchscreen (Goodix `27C6:012D`) is touch-only on this model.
- Key and collection offsets are specific to this HID descriptor. For a different
  machine, re-derive `PEN_RDESC` and the in-range bit — see
  [`docs/diagnosis.md`](docs/diagnosis.md).

## Upstream

Reported to the kernel HID maintainers as
[kernel Bugzilla #222273](https://bugzilla.kernel.org/show_bug.cgi?id=222273):

> **[HID] Elan/Wacom 04F3:3355 haptic touchpad / pen hybrid: Touchpad works, but
> pen digitizer collection is dropped**

`Drivers → Input Devices`, status NEW. The reproducer is the third section of
[`docs/diagnosis.md`](docs/diagnosis.md): the pen node exists and works
perfectly as soon as `hid_multitouch` is unloaded, which puts the bug squarely
in `drivers/hid/hid-multitouch.c` returning early for collections that are
neither `HID_DG_TOUCHSCREEN` nor `HID_DG_TOUCHPAD`.

If you are a HID maintainer looking at that report: this repo is the userspace
proof-of-concept, not a kernel fix. Testers welcome — please comment on the bug.

## Licence

MIT — see [LICENSE](LICENSE).
