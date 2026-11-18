#!/usr/bin/env python3
"""Yoga Pen 2 bridge for the Lenovo Yoga Pro 7 15ASH11 Force Pad.

The haptic Force Pad (ACPI WACF2205, ELAN i2c-HID 04F3:3355) is both the
touchpad and the pen digitizer. They share one HID device: the touchpad is the
"Touch Pad" application collection (report ID 0x52) and the pen is a separate
"Digitizer" application collection (report ID 0x12).

hid-multitouch claims that device for the touchpad and never exports the pen
collection, so the kernel creates no input device for the pen at all - no
BTN_TOOL_PEN, no pressure, nothing for libinput or any application to use.
The reports themselves are fine and hid-generic maps that collection correctly
(verified by unloading hid_multitouch), so this bridge replays the pen
collection through /dev/uhid and lets the kernel's own hid-input build the
tablet device.

It also grabs the touchpad input node (EVIOCGRAB) while the pen is in range, so
a resting hand cannot move the pointer while you write; the grab is released
shortly after the pen leaves range.

Requires: /dev/uhid and the WACF2205 hidraw + touchpad event nodes readable by
the user (see udev/70-yoga-pen.rules). No root needed at runtime.

Options:
  --no-grab        do not suppress the touchpad while the pen is in range
  --verbose        log every forwarded report
"""

import argparse
import fcntl
import os
import select
import struct
import sys
import time

HID_ID = "0018:000004F3:00003355"
HID_PHYS = "i2c-WACF2205:00"
PEN_REPORT_ID = 0x12
PEN_INRANGE_BIT = 0x20
GRAB_RELEASE_DELAY = 0.7  # seconds after the pen leaves range

UHID_START = 2
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14

HID_MAX_DESCRIPTOR_SIZE = 4096
EVIOCGRAB = 0x40044590

# The pen's "Digitizer" application collection, copied byte-for-byte from this
# device's HID report descriptor, minus the vendor Feature report (report ID
# 0x11) that nothing would answer.
PEN_RDESC = bytes.fromhex(
    "050d0901a1018512"
    "35000920a100"
    "094209440945093c095a0932"
    "15002501750195068102"
    "0600ff0944095a95028102"
    "05010930268c23466c396511550d751095018142"
    "093146f0232658168102"
    "450065005500"
    "050d093075102600108102"
    "0600ff095b16008026ff7f75108102"
    "050d095b170000008027ffffff7f75208102"
    "0600ff0900750826ff0015008102"
    "050d093b8102"
    "6514550e16d8dc26282336d8dc4628237510093d8102093e8102"
    "c0"
    "c0"
)

VERBOSE = False


def log(msg):
    print(f"yoga-pen: {msg}", flush=True)


def find_hidraw():
    """The real I2C-HID node: match HID_ID and HID_PHYS so we never pick up the
    virtual uhid device this bridge creates (it shares vendor/product)."""
    try:
        nodes = os.listdir("/sys/class/hidraw")
    except OSError:
        return None
    for node in sorted(nodes):
        try:
            txt = open(f"/sys/class/hidraw/{node}/device/uevent").read()
        except OSError:
            continue
        if HID_ID in txt and HID_PHYS in txt:
            return "/dev/" + node
    return None


def find_touchpad_event():
    """event node of the Touchpad input under the same HID device."""
    try:
        nodes = os.listdir("/sys/class/hidraw")
    except OSError:
        return None
    for node in sorted(nodes):
        dev = f"/sys/class/hidraw/{node}/device"
        try:
            if HID_PHYS not in open(f"{dev}/uevent").read():
                continue
            inputs = os.listdir(f"{dev}/input")
        except OSError:
            continue
        for inp in inputs:
            base = f"{dev}/input/{inp}"
            try:
                if not open(f"{base}/name").read().strip().endswith("Touchpad"):
                    continue
                for child in os.listdir(base):
                    if child.startswith("event"):
                        return "/dev/input/" + child
            except OSError:
                continue
    return None


def make_uhid():
    fd = os.open("/dev/uhid", os.O_RDWR)
    # The HID name becomes the input device name and, because the descriptor
    # carries a Battery Strength usage, the power_supply model shown in KDE's
    # Power & Battery panel.
    name = b"Yoga Pen 2"
    phys = b"i2c-WACF2205:00/input0"
    req = (
        struct.pack("<I", UHID_CREATE2)
        + name.ljust(128, b"\0")
        + phys.ljust(64, b"\0")
        + b"\0" * 64
        + struct.pack("<HHIIII", len(PEN_RDESC), 0x18, 0x04F3, 0x3355, 0x0100, 0)
        + PEN_RDESC.ljust(HID_MAX_DESCRIPTOR_SIZE, b"\0")
    )
    os.write(fd, req)
    return fd


def send_input(uhid, data):
    os.write(uhid, struct.pack("<I", UHID_INPUT2) + struct.pack("<H", len(data)) + data)


class PadGrab:
    """Suppress the touchpad while the pen is in range."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.fd = None
        self.grabbed = False

    def open(self):
        if not self.enabled or self.fd is not None:
            return
        path = find_touchpad_event()
        if not path:
            return
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        log(f"touchpad node {path}")

    def grab(self):
        if not self.enabled or self.grabbed:
            return
        self.open()
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1)
            self.grabbed = True
            log("pen in range: touchpad suppressed")
        except OSError as e:
            log(f"grab failed: {e}")
            self.fd = None

    def release(self):
        if not self.grabbed:
            return
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 0)
            log("pen left range: touchpad restored")
        except OSError as e:
            log(f"release failed: {e}")
        self.grabbed = False


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description="Bridge the Yoga Pen 2 out of the Force Pad HID device")
    ap.add_argument("--no-grab", action="store_true", help="do not suppress the touchpad while the pen is in range")
    ap.add_argument("--verbose", action="store_true", help="log every forwarded report")
    args = ap.parse_args()
    VERBOSE = args.verbose

    pad = PadGrab(enabled=not args.no_grab)
    poller = select.poll()

    def open_hid():
        path = find_hidraw()
        if not path:
            return False
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        poller.register(fd, select.POLLIN)
        log(f"pen reports from {path}")
        return fd

    hid = open_hid()
    while hid is None:
        log("waiting for the WACF2205 hidraw node")
        time.sleep(2)
        hid = open_hid()

    uhid = make_uhid()
    poller.register(uhid, select.POLLIN)
    pad.open()

    in_range = False
    leave_at = None
    forwarded = 0

    while True:
        for fd, _mask in poller.poll(300):
            if fd == uhid:
                ev = os.read(uhid, 4380)
                if not ev:
                    raise SystemExit("uhid device closed")
                etype = struct.unpack_from("<I", ev, 0)[0]
                if etype == UHID_GET_REPORT:
                    rid = struct.unpack_from("<I", ev, 4)[0]
                    os.write(uhid, struct.pack("<I", UHID_GET_REPORT_REPLY)
                             + struct.pack("<IHH", rid, 2, 0))  # -ENOENT
                elif etype == UHID_SET_REPORT:
                    rid = struct.unpack_from("<I", ev, 4)[0]
                    os.write(uhid, struct.pack("<I", UHID_SET_REPORT_REPLY)
                             + struct.pack("<IH", rid, 0))
                elif etype == UHID_START:
                    log("virtual pen device started")
                continue

            try:
                data = os.read(hid, 4096)
            except BlockingIOError:
                continue

            if not data:
                # device went away (suspend, module reload): rebuild
                log("hidraw node closed, reconnecting")
                poller.unregister(hid)
                os.close(hid)
                pad.release()
                hid = None
                while hid is None:
                    time.sleep(2)
                    hid = open_hid()
                old, uhid = uhid, make_uhid()
                poller.unregister(old)
                os.close(old)
                poller.register(uhid, select.POLLIN)
                continue

            if data[0] != PEN_REPORT_ID:
                continue

            send_input(uhid, data)
            forwarded += 1
            if VERBOSE:
                log(f"pen {data.hex()}")
            elif forwarded % 2000 == 0:
                log(f"forwarded {forwarded} pen reports")

            if len(data) > 1:
                inrange = bool(data[1] & PEN_INRANGE_BIT)
                if inrange:
                    leave_at = None
                    if not in_range:
                        in_range = True
                        pad.grab()
                elif in_range:
                    in_range = False
                    leave_at = time.monotonic() + GRAB_RELEASE_DELAY

        if leave_at is not None and time.monotonic() >= leave_at:
            pad.release()
            leave_at = None


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
