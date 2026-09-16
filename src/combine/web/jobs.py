"""Work that takes longer than a request is allowed to take.

A league-wide trade search reads every roster and then searches over the pairs,
which runs to the best part of a minute and sometimes past it. Held open as one
request that is two separate failures.

The first is Cloudflare, which gives an origin 100 seconds to answer and then
serves a 524 to the person waiting. It is not configurable on this plan, and it
would be the wrong thing to raise anyway: a proxy is right to assume a minute
and a half of silence means something has died.

The second is quieter and worse. The handlers were `async def` around blocking
calls, so the search occupied the event loop for its whole run and every other
request — a tab switch, a score refresh — queued behind it. The app looked
broken to anyone who touched it while a search was going.

So the search runs on a thread and the request that asked for it returns at
once with a ticket. The browser polls, each poll is a normal fast request, and
nothing anywhere is waiting on anything for more than a moment.

Deliberately not a queue, a broker or a worker pool. This serves one person on
one machine; a dictionary and a thread are the whole of what that needs, and
anything more would be infrastructure to maintain rather than a feature.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# How long a finished result stays collectable. Long enough to survive a phone
# locking mid-search and being picked up again, short enough that a day of
# searching does not sit in memory.
LIFE = 900.0

_LOCK = threading.Lock()
_JOBS: dict[str, Job] = {}
_RUNNING: dict[tuple, str] = {}     # key -> id, so a double tap joins rather than doubles


@dataclass
class Job:
    id: str
    key: tuple
    state: str = "running"          # running | done | failed
    value: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started


def _sweep(now: float) -> None:
    """Caller holds the lock."""
    for jid in [j.id for j in _JOBS.values()
                if j.finished and now - j.finished > LIFE]:
        _JOBS.pop(jid, None)


def start(key: tuple, build: Callable[[], Any]) -> str:
    """Run `build()` on a thread and hand back a ticket.

    An identical request already in flight hands back that one's ticket instead
    of starting a second: pressing the button twice is a thing people do, and
    the honest answer to the second press is the first press's result.
    """
    now = time.time()
    with _LOCK:
        _sweep(now)
        live = _RUNNING.get(key)
        if live and live in _JOBS and _JOBS[live].state == "running":
            return live
        job = Job(id=uuid.uuid4().hex[:16], key=key)
        _JOBS[job.id] = job
        _RUNNING[key] = job.id

    def run() -> None:
        try:
            value, err = build(), ""
        except Exception as exc:                      # noqa: BLE001
            # Whatever went wrong, it goes back to the person as a sentence
            # rather than into a log nobody reads.
            value, err = None, f"{type(exc).__name__}: {exc}"
        with _LOCK:
            job.value, job.error = value, err
            job.state = "failed" if err else "done"
            job.finished = time.time()
            if _RUNNING.get(key) == job.id:
                _RUNNING.pop(key, None)

    threading.Thread(target=run, daemon=True, name=f"job-{job.id}").start()
    return job.id


def get(jid: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(jid)


def forget() -> None:
    """Drop everything. What the refresh button calls, alongside the memo."""
    with _LOCK:
        _JOBS.clear()
        _RUNNING.clear()
