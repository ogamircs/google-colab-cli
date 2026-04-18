"""Async-to-sync bridge: a daemon thread running a private asyncio loop.

Every public Python API method schedules its coroutine onto the runner's
loop via ``asyncio.run_coroutine_threadsafe`` and blocks on the future.
This works uniformly from plain scripts, Jupyter notebooks (which already
run an event loop), and async callers.
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import os
import threading
from collections.abc import Awaitable
from typing import TypeVar

T = TypeVar("T")


class SyncRunner:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._serve,
            name="colab-cli-api-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        self._closed = False

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    def run(self, coro: Awaitable[T], *, timeout: float | None = None) -> T:
        if self._closed:
            raise RuntimeError("SyncRunner is closed")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            return
        self._thread.join(timeout=5)


_runner: SyncRunner | None = None
_runner_lock = threading.Lock()


def get_runner() -> SyncRunner:
    global _runner
    with _runner_lock:
        if _runner is None or _runner._closed:
            _runner = SyncRunner()
    return _runner


def reset_runner() -> None:
    """Drop the singleton. Used by tests and after fork."""
    global _runner
    with _runner_lock:
        existing = _runner
        _runner = None
    if existing is not None:
        existing.close()


def _reset_in_child() -> None:
    # Post-fork children cannot reuse the parent's thread; start fresh lazily.
    global _runner
    _runner = None


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_in_child)

atexit.register(reset_runner)
