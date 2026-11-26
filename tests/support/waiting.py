"""Polling helpers for state that changes on another thread."""

import time
from collections.abc import Callable


def wait_until(
    condition: Callable[[], object], *, timeout: float, interval: float = 0.01
) -> bool:
    """Poll until condition() is true or timeout passes; return its final value."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(interval)
    return bool(condition())
