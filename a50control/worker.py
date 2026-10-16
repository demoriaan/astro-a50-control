"""Background thread that owns all HID I/O and reports back on the GTK loop."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable

from gi.repository import GLib

Job = Callable[[Any], Any]


class Worker:
    """Runs jobs sequentially; a newer job with the same key replaces a pending one.

    Coalescing keeps slider drags responsive: only the latest value of each
    control is sent once the device is ready for the next request.
    """

    def __init__(self) -> None:
        self.device = None
        self._jobs: OrderedDict[Any, tuple[Job, Callable | None, Callable | None, bool]] = OrderedDict()
        self._cond = threading.Condition()
        self._anon = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="a50-io", daemon=True)
        self._thread.start()

    def submit(self, key: Any, job: Job, done: Callable | None = None, error: Callable | None = None,
               needs_device: bool = True) -> None:
        with self._cond:
            if key is None:
                self._anon += 1
                key = ("anon", self._anon)
            self._jobs[key] = (job, done, error, needs_device)
            self._cond.notify()

    def pending(self, key: Any) -> bool:
        with self._cond:
            return key in self._jobs

    def clear(self) -> None:
        with self._cond:
            self._jobs.clear()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._jobs.clear()
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._jobs and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                _key, (job, done, error, needs_device) = self._jobs.popitem(last=False)
            if needs_device and self.device is None:
                continue  # device went away after the job was queued
            try:
                result = job(self.device)
            except Exception as exc:  # noqa: BLE001 - forwarded to the UI
                if error is not None:
                    GLib.idle_add(_call, error, exc)
                continue
            if done is not None:
                GLib.idle_add(_call, done, result)


def _call(fn: Callable, arg: Any) -> bool:
    fn(arg)
    return GLib.SOURCE_REMOVE
