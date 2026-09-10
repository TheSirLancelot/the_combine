"""Per-position projection correction.

The correction exists because ESPN over-projects defensive backs and linebackers
by one to two points in an IDP league while being fine on offense. Comparing a
cornerback to a defensive end on raw projections therefore gives the wrong
answer, not an imprecise one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from combine import db
from combine.pipeline.calibration import EMPTY, MIN_SAMPLE, Bias, Calibration, measure


def bias(pos, n, mean, se):
    return Bias(pos=pos, n=n, mean=mean, se=se)


def test_a_small_sample_is_not_a_correction():
    assert not bias("DT", MIN_SAMPLE - 1, -3.0, 0.1).real


def test_a_bias_inside_two_standard_errors_is_not_a_correction():
    """ESPN being 0.4 off with a 0.4 standard error is ESPN being fine."""
    assert not bias("QB", 300, -0.45, 0.44).real
    assert bias("S", 204, -2.05, 0.41).real


def test_offset_is_zero_for_anything_unmeasured_or_insignificant():
    cal = Calibration("rcl", 2025, {"S": bias("S", 204, -2.05, 0.41),
                                    "QB": bias("QB", 337, -0.45, 0.44)})
    assert cal.offset("S") == -2.05
    assert cal.offset("QB") == 0.0
    assert cal.offset("WR") == 0.0        # never measured
    assert cal.offset("") == 0.0


def test_adjust_reverses_the_wrong_answer():
    """The case that motivated this: a corner projected 9.5 against an end
    projected 7.4. Raw says the corner is better by 2.1; corrected says the end
    is better."""
    cal = Calibration("rcl", 2025, {"CB": bias("CB", 112, -1.71, 0.51),
                                    "DE": bias("DE", 216, 0.60, 0.42)})
    corner, end = cal.adjust("CB", 9.5), cal.adjust("DE", 7.4)
    assert 9.5 > 7.4
    assert corner < end + 0.6      # DE bias is not significant, so it stays 7.4
    assert round(corner, 2) == 7.79


def test_no_history_means_no_correction_rather_than_an_error():
    assert EMPTY.offset("CB") == 0.0
    assert EMPTY.adjust("CB", 9.5) == 9.5
    assert EMPTY.active == {}


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "c.db"
    db.ensure_schema(path)
    c = db.connect(path)
    yield c
    c.close()


def add(conn, week, pos, projected, actual, played=1, league="rcl"):
    conn.execute(
        "INSERT INTO espn_player_week (league, season, week, espn_id, fantasy_team,"
        " name, pos, slot, started, projected, actual, played, pulled_at)"
        " VALUES (?,2025,?,?,'T','P',?,?,1,?,?,?, 'now')",
        (league, week, f"{pos}{week}{projected}{actual}", pos, pos,
         projected, actual, played))
    conn.commit()


def test_measure_ignores_zero_projections_and_unplayed_games(conn):
    """A zero projection is ESPN declining to make a claim, and an unplayed game
    is not evidence about a projection."""
    for week in range(1, 45):
        add(conn, week, "LB", 10.0, 8.0)
    add(conn, 1, "LB", 0.0, 30.0)          # no claim
    add(conn, 2, "LB", 10.0, 99.0, played=0)   # not evidence
    cal = measure(conn, "rcl", 2025)
    assert cal.biases["LB"].n == 44
    assert round(cal.biases["LB"].mean, 2) == -2.0


def test_before_week_excludes_the_week_being_predicted(conn):
    """Calibrating on the week you are scoring makes any correction look good."""
    for week in range(1, 45):
        add(conn, week, "LB", 10.0, 8.0)
    for i in range(40):
        add(conn, 50 + i, "LB", 10.0, 30.0)     # a wildly different later block
    early = measure(conn, "rcl", 2025, before_week=45)
    assert round(early.biases["LB"].mean, 2) == -2.0
    everything = measure(conn, "rcl", 2025)
    assert everything.biases["LB"].mean > -2.0


def test_leagues_are_measured_separately(conn):
    for week in range(1, 45):
        add(conn, week, "WR", 10.0, 8.0, league="rcl")
        add(conn, week, "WR", 10.0, 12.0, league="dmwd")
    assert measure(conn, "rcl", 2025).biases["WR"].mean < 0
    assert measure(conn, "dmwd", 2025).biases["WR"].mean > 0
