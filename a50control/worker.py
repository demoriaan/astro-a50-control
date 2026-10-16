"""Background thread that owns all HID I/O and reports back on the GTK loop."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from gi.repository import GLib

Job = Callable[[Any], Any]


class Worker:
    """Runs jobs sequentially; a newer job with the same key replaces a pending one.

    Coalescing keeps slider drags responsive: only the latest value of each
    control is sent once the device is ready for the next request. A replaced
    job moves to the end of the queue so it never overtakes later work.
    """

    def __init__(self) -> None:
        self.device = None
        self._jobs: OrderedDict[Any, tuple[Job, Callable | None, Callable | None, bool]] = OrderedDict()
        self._cond = threading.Condition()
        self._anon = 0
        self._stop = False
        self._drain_until = 0.0
        self._thread = threading.Thread(target=self._run, name="a50-io", daemon=True)
        self._thread.start()

    def submit(self, key: Any, job: Job, done: Callable | None = None, error: Callable | None = None,
               needs_device: bool = True) -> None:
        """Queue ``job(device)``; ``done(result)`` / ``error(exc, device)`` run on the GTK loop."""
        with self._cond:
            if self._stop:
                return
            if key is None:
                self._anon += 1
                key = ("anon", self._anon)
            self._jobs.pop(key, None)
            self._jobs[key] = (job, done, error, needs_device)
            self._cond.notify()

    def pending(self, key: Any) -> bool:
        with self._cond:
            return key in self._jobs

    def clear(self) -> None:
        with self._cond:
            self._jobs.clear()

    def shutdown(self, flush_timeout: float = 2.0) -> None:
        """Finish queued writes (bounded), close the device and stop the thread."""
        with self._cond:
            self._stop = True
            self._drain_until = time.monotonic() + flush_timeout
            for key in [k for k in self._jobs if k == "poll" or k == "refresh"]:
                del self._jobs[key]
            self._cond.notify()
        self._thread.join(flush_timeout + 1.0)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._jobs and not self._stop:
                    self._cond.wait()
                if not self._jobs or (self._stop and time.monotonic() > self._drain_until):
                    break
                _key, (job, done, error, needs_device) = self._jobs.popitem(last=False)
                dev = self.device
            if needs_device and dev is None:
                continue  # the device went away after the job was queued
            try:
                result = job(dev)
            except Exception as exc:  # noqa: BLE001 - forwarded to the UI
                if error is not None and not self._stop:
                    GLib.idle_add(_call, error, exc, dev)
                continue
            if done is not None and not self._stop:
                GLib.idle_add(_call, done, result)
        if self.device is not None:
            self.device.close()
            self.device = None


def _call(fn: Callable, *args: Any) -> bool:
    fn(*args)
    return GLib.SOURCE_REMOVE
