"""Work that outlives its request.

The failure this exists to prevent is a 524: Cloudflare gives the origin 100
seconds and a full trade search sometimes wants more. These are about the
runner keeping its side of that bargain — every request short, the work
unbothered by who is still watching.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.web import jobs


@pytest.fixture(autouse=True)
def clean():
    jobs.forget()
    yield
    jobs.forget()


def settled(jid, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        job = jobs.get(jid)
        if job and job.state != "running":
            return job
        time.sleep(0.005)
    raise AssertionError("job never settled")


def test_start_returns_at_once_and_the_work_happens_behind_it():
    """The whole point: the caller is not the one waiting."""
    gate = threading.Event()

    def build():
        gate.wait(2)
        return "done"

    jid = jobs.start(("slow",), build)
    assert jobs.get(jid).state == "running"
    gate.set()
    assert settled(jid).value == "done"


def test_a_finished_job_hands_back_what_the_work_returned():
    jid = jobs.start(("k",), lambda: ("deals", {"deals": [1, 2]}))
    job = settled(jid)
    assert job.state == "done"
    assert job.value == ("deals", {"deals": [1, 2]})
    assert job.error == ""


def test_a_failure_comes_back_as_a_sentence_not_a_traceback():
    """It has to reach the person. A stack trace in a log nobody reads is the
    same as the search silently never finishing."""
    def boom():
        raise ValueError("ESPN said no")

    job = settled(jobs.start(("bad",), boom))
    assert job.state == "failed"
    assert job.error == "ValueError: ESPN said no"
    assert job.value is None


def test_pressing_the_button_twice_joins_the_first_search():
    """People do press it twice, and the honest answer to the second press is
    the first press's result rather than the same minute of work again."""
    runs = []
    gate = threading.Event()

    def build():
        runs.append(1)
        gate.wait(2)
        return "x"

    first = jobs.start(("same",), build)
    second = jobs.start(("same",), build)
    assert first == second
    gate.set()
    settled(first)
    assert len(runs) == 1


def test_a_different_question_gets_its_own_job():
    gate = threading.Event()
    a = jobs.start(("find", "rcl"), lambda: gate.wait(2))
    b = jobs.start(("find", "dmwd"), lambda: gate.wait(2))
    assert a != b
    gate.set()
    settled(a); settled(b)


def test_asking_again_after_one_finished_runs_it_again():
    """Joining is for a search in flight. A finished one is a cached answer the
    memo owns, not something this should silently re-serve forever."""
    first = jobs.start(("k",), lambda: "one")
    settled(first)
    second = jobs.start(("k",), lambda: "two")
    assert second != first
    assert settled(second).value == "two"


def test_an_unknown_ticket_is_simply_absent():
    assert jobs.get("nothing-like-this") is None


def test_results_are_swept_once_they_are_old():
    jid = jobs.start(("k",), lambda: "v")
    settled(jid)
    jobs.get(jid).finished = time.time() - jobs.LIFE - 1
    jobs.start(("other",), lambda: "v")       # any start sweeps
    assert jobs.get(jid) is None


def test_elapsed_keeps_running_until_it_stops():
    gate = threading.Event()
    jid = jobs.start(("k",), lambda: gate.wait(2))
    time.sleep(0.05)
    assert jobs.get(jid).elapsed > 0
    gate.set()
    job = settled(jid)
    assert job.elapsed == pytest.approx(job.finished - job.started)
