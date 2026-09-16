"""A memo with a clock, because the expensive calls are all reads.

Streamlit's `cache_data` is a decorator around the same idea plus a lot of
machinery for reruns that do not exist here. This is the small version: a dict,
a timestamp, and a version token so an edit to the pipeline invalidates
everything without anybody remembering to.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from threading import Lock

_LOCK = Lock()
_HELD: dict[tuple, tuple[float, object]] = {}


def code_version() -> str:
    """Changes whenever anything under `combine` changes on disk.

    Same trick the Streamlit app uses and for the same reason: a cached value
    built by older code outliving the code that built it is a bug that presents
    as an impossible one.
    """
    root = Path(__file__).resolve().parents[1]
    stamps = sorted(f"{p}:{p.stat().st_mtime_ns}"
                    for p in root.rglob("*.py"))
    return hashlib.sha1("|".join(stamps).encode(),
                        usedforsecurity=False).hexdigest()[:12]


def memo(key: tuple, ttl: float, build):
    """`build()` at most once per `ttl` seconds for this key.

    The lock is held only around the dictionary, never around `build`. Holding
    it across a 50 second trade search would make every other request queue
    behind it, which on a phone reads as the app being broken.
    """
    full = (*key, code_version())
    now = time.time()
    with _LOCK:
        hit = _HELD.get(full)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _LOCK:
        _HELD[full] = (now, value)
    return value


def forget() -> None:
    """Drop everything. What the refresh button calls."""
    with _LOCK:
        _HELD.clear()
