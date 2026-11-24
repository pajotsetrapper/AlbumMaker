# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Checking in the background whether FFmpeg is missing.

Looking for a program means searching every folder on the PATH, which can be
slow (a network drive or sleeping disk on the PATH holds the search up), and the
window used to wait for it before it appeared and again every time it was
brought to the front. Here a background thread does the search, the window
collects the answer whenever it likes (poll()), and a recent answer is reused,
so switching back to the window does not search again.

The thread is a daemon thread: one stuck on a dead share must not stop the app
from closing.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional


def _ffmpeg_missing() -> bool:
    from ..ffmpeg_setup import ffmpeg_missing      # imported in the thread, not at start-up
    return ffmpeg_missing()


class FfmpegChecker:
    def __init__(self, check: Optional[Callable[[], bool]] = None, max_age: float = 5.0,
                 clock: Callable[[], float] = time.monotonic):
        self._check = check or _ffmpeg_missing
        self._max_age = max_age
        self._clock = clock
        self._results: "queue.SimpleQueue[bool]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._running = False
        self._again = False         # a forced check was asked for while one was running
        self._known: Optional[bool] = None
        self._known_at = 0.0

    def request(self, force: bool = False) -> bool:
        """Start a check, unless one is already running or the last answer is
        recent. `force` skips the recent-answer rule (use it after installing
        FFmpeg); asked while a check is running, it makes that thread check once
        more when it finishes, as its search may have started before the change.
        True when a check was started."""
        with self._lock:
            if self._running:
                if force:
                    self._again = True
                return False
            if not force and self._fresh_locked() is not None:
                return False
            self._running = True
        threading.Thread(target=self._work, daemon=True).start()
        return True

    def _fresh_locked(self) -> Optional[bool]:
        if self._known is not None and self._clock() - self._known_at <= self._max_age:
            return self._known
        return None

    def fresh(self) -> Optional[bool]:
        """The last answer (True = FFmpeg is missing) if it is recent, else None."""
        with self._lock:
            return self._fresh_locked()

    def _work(self) -> None:
        while True:
            try:
                missing: Optional[bool] = bool(self._check())
            except Exception:       # a failed search is no answer; the button keeps its state
                missing = None
            with self._lock:
                if missing is not None:
                    self._known, self._known_at = missing, self._clock()
                    self._results.put(missing)
                if self._again:
                    self._again = False
                    continue
                self._running = False
                return

    def poll(self) -> Optional[bool]:
        """The newest answer since the last call (True = FFmpeg is missing), or None."""
        answer = None
        while True:
            try:
                answer = self._results.get_nowait()
            except queue.Empty:
                return answer

    @property
    def busy(self) -> bool:
        """True while a check is running or an answer has not been collected yet."""
        with self._lock:
            return self._running or not self._results.empty()
