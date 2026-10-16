"""Raw hidraw transport for the A50 base station (no third-party deps)."""
from __future__ import annotations

import errno
import fcntl
import os
import select
import threading
import time
from pathlib import Path

from . import protocol as p

SYSFS_HIDRAW = Path("/sys/class/hidraw")


class DeviceNotFound(Exception):
    pass


class DevicePermissionError(Exception):
    def __init__(self, path: str):
        super().__init__(f"no permission to open {path}")
        self.path = path


class DeviceBusy(Exception):
    """Another process (e.g. the GUI) already talks to the base station."""


class DeviceGone(Exception):
    pass


class Timeout(Exception):
    pass


def _hid_id(node: Path) -> tuple[int, int] | None:
    try:
        uevent = (node / "device" / "uevent").read_text()
    except OSError:
        return None
    for line in uevent.splitlines():
        if line.startswith("HID_ID="):
            _bus, vid, pid = line.split("=", 1)[1].split(":")
            return int(vid, 16), int(pid, 16)
    return None


def _has_vendor_page(node: Path) -> bool:
    try:
        desc = (node / "device" / "report_descriptor").read_bytes()
    except OSError:
        return True  # cannot tell; let the open attempt decide
    page = p.USAGE_PAGE.to_bytes(2, "little")
    return bytes([0x06]) + page in desc


def find_devices() -> list[str]:
    """Return /dev paths of all connected A50 Gen 4 vendor interfaces."""
    found = []
    if not SYSFS_HIDRAW.is_dir():
        return found
    for node in sorted(SYSFS_HIDRAW.iterdir()):
        if _hid_id(node) == (p.VENDOR_ID, p.PRODUCT_ID) and _has_vendor_page(node):
            found.append(f"/dev/{node.name}")
    return found


class Transport:
    """Serialised request/response channel over one hidraw node."""

    def __init__(self, path: str):
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except PermissionError as exc:
            raise DevicePermissionError(path) from exc
        except FileNotFoundError as exc:
            raise DeviceNotFound(path) from exc
        try:
            # Every open hidraw fd receives every input report, so a second
            # client would see (and could mistake) our responses. Stay exclusive.
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            raise DeviceBusy(f"{path} is in use by another A50 Control instance") from exc
        self.path = path
        self._seq = 0
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1

    def _read(self, timeout: float) -> bytes | None:
        try:
            ready, _, _ = select.select([self.fd], [], [], max(timeout, 0))
            if not ready:
                return None
            return os.read(self.fd, p.REPORT_SIZE)
        except OSError as exc:
            raise DeviceGone(str(exc)) from exc

    def _drain(self) -> None:
        while self._read(0) is not None:
            pass

    def transact(self, cmd: int, payload: bytes = b"", timeout: float = 1.0, retries: int = 1) -> bytes:
        """Send one command and return the data bytes of its response."""
        with self._lock:
            if self.fd < 0:
                raise DeviceGone("transport closed")
            for attempt in range(retries + 1):
                seq = self._seq
                self._seq = (self._seq + 1) & 0x03
                self._drain()
                try:
                    os.write(self.fd, p.encode_request(cmd, payload, seq))
                except OSError as exc:
                    if exc.errno in (errno.ENODEV, errno.EIO, errno.EPIPE):
                        raise DeviceGone(str(exc)) from exc
                    raise
                deadline = time.monotonic() + timeout
                while True:
                    report = self._read(deadline - time.monotonic())
                    if report is None:
                        break
                    if report[0] == p.REPORT_ID and p.response_seq(report) == seq:
                        return p.decode_response(cmd, report)
            raise Timeout(f"no response to command 0x{cmd:02x}")
