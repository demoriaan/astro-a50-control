#!/usr/bin/env python3
"""Read-only protocol probe for the ASTRO A50 Gen 4 base station.

Sends only *read* commands recovered from AstroCommandCenter.exe and dumps the
raw responses. Usage: probe.py [/dev/hidrawN]
"""
import os
import select
import sys

REPORT_ID = 0x02
REPORT_LEN = 64


class Dev:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR)
        self.seq = 0

    def drain(self):
        while select.select([self.fd], [], [], 0.05)[0]:
            data = os.read(self.fd, REPORT_LEN)
            print("  (unsolicited)", data.hex(" "))

    def xfer(self, cmd, payload=b""):
        seq = self.seq & 0x03
        self.seq += 1
        body = bytes([cmd, (seq << 6) | (len(payload) & 0x3F)]) + bytes(payload)
        pkt = bytes([REPORT_ID]) + body
        pkt = pkt + bytes(REPORT_LEN - len(pkt))
        self.drain()
        os.write(self.fd, pkt)
        while True:
            r, _, _ = select.select([self.fd], [], [], 1.0)
            if not r:
                return None
            data = os.read(self.fd, REPORT_LEN)
            if data[0] == REPORT_ID and (data[2] >> 6) == seq:
                return data
            print("  (skipped)", data.hex(" "))


def show(dev, name, cmd, payload=b""):
    resp = dev.xfer(cmd, payload)
    if resp is None:
        print(f"{name:28s} cmd=0x{cmd:02x} {bytes(payload).hex()}: TIMEOUT")
        return None
    n = resp[2] & 0x3F
    print(f"{name:28s} cmd=0x{cmd:02x} {bytes(payload).hex():6s} -> status=0x{resp[1]:02x} "
          f"len={n:2d} data={resp[3:3 + n].hex(' ')}")
    return resp


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/dev/hidraw3"
    dev = Dev(path)
    show(dev, "battery", 0x7C)
    show(dev, "current mix", 0x72)
    show(dev, "current ui", 0x72, b"\x01")
    show(dev, "current eq", 0x6C)
    show(dev, "default mode", 0x6B)
    show(dev, "default balance[0]", 0x77, b"\x00")
    show(dev, "default balance[1]", 0x77, b"\x01")
    show(dev, "brightness", 0x79)
    show(dev, "accel timeout", 0x78)
    show(dev, "tone level[0]", 0x7A, b"\x00")
    show(dev, "tone level[1]", 0x7A, b"\x01")
    show(dev, "noise gate", 0x6A)
    show(dev, "mic eq", 0x7B)
    for sid in range(6):
        show(dev, f"slider {sid}", 0x68, bytes([sid]))
    for p in (1, 2, 3):
        show(dev, f"eq params {p}", 0x69, bytes([p]))
        for idx in (0, 1):
            show(dev, f"eq name {p}/{idx}", 0x6E, bytes([p, idx]))
        for band in range(1, 6):
            show(dev, f"eq coeff {p}/{band}", 0x70, bytes([p, band]))


if __name__ == "__main__":
    main()
