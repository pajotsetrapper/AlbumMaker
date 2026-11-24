# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Pieter Remi Coppens
"""Checking in the background whether the trip folders still exist.

Asking the operating system about a folder on a sleeping NAS, a network share
that is offline or an unplugged disk can take many seconds. Done while the
window is being built, that holds up the window for every such folder. Here the
questions are asked by background threads, and the window collects the answers
whenever it likes (poll()), so it never waits.

The threads are daemon threads on purpose: one stuck on a dead share must not
stop the app from closing.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path


class FolderChecker:
    def __init__(self, workers: int = 8):
        self._workers = workers
        self._results: "queue.SimpleQueue[tuple[str, bool]]" = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._pending = 0           # folders asked about and not yet answered

    def start(self, paths) -> None:
        """Begin checking `paths` (strings); more can be added at any time."""
        jobs: "queue.SimpleQueue[str]" = queue.SimpleQueue()
        paths = list(paths)
        for path in paths:
            jobs.put(path)
        with self._lock:
            self._pending += len(paths)
        for _ in range(min(self._workers, len(paths))):
            threading.Thread(target=self._work, args=(jobs,), daemon=True).start()

    def _work(self, jobs) -> None:
        while True:
            try:
                path = jobs.get_nowait()
            except queue.Empty:
                return
            try:
                found = Path(path).expanduser().is_dir()
            except OSError:
                found = False
            self._results.put((path, found))

    def poll(self) -> list:
        """[(path, exists)] for every folder answered since the last call."""
        answers = []
        while True:
            try:
                answers.append(self._results.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            self._pending -= len(answers)
        return answers

    @property
    def busy(self) -> bool:
        """True while some folder has not been answered (and collected) yet."""
        with self._lock:
            return self._pending > 0
