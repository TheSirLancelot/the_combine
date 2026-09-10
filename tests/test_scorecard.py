"""Grading the tool on live data.

Every claim this system makes -- the optimizer's win rate, the waiver wire's
points a week, the 0.25 threshold -- was measured on 2025 and none of it has
been checked against a real decision. This is what makes that possible, so the
things it must not do are as important as what it does: it must not restate a
projection after the fact, must not invent an outcome it could not resolve, and
must never be able to take a Sunday morning check down with it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import db
from combine.pipeline import scorecard
from combine.pipeline.scorecard import Row


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "t.db"
    db.ensure_schema(path)
    c = db.connect(path)
    yield c
    c.close()


def player_week(conn, league, week, espn_id, name, actual, projected=10.0):
    conn.execute(
        "INSERT OR REPLACE INTO espn_player_week (league, season, week, espn_id,"
        " fantasy_team, versus, name, pos, slot, eligible, started, projected,"
        " actual, played, pulled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (league, 2026, week, espn_id, "me", "them", name, "WR", "WR", "WR,BE",
         1, projected, actual, 1, db.now()))
    conn.commit()


def a_row(**kw):
    base = {"kind": "start", "subject_id": "11", "subject_name": "Bench Guy",
            "against_id": "22", "against_name": "Starter", "subject_proj": 12.0,
            "against_proj": 10.0, "edge": 2.0, "bar": 1.8}
    return Row(**{**base, **kw})


def test_a_recommendation_is_recorded_once_with_the_numbers_as_advised(conn):
    """ESPN moves projections through the day. Re-running the command must not
    overwrite the call being graded, or the tool gets marked against numbers it
    did not have."""
    assert scorecard.record("rcl", 2026, 2, [a_row()], conn=conn) == 1
    assert scorecard.record("rcl", 2026, 2,
                            [a_row(subject_proj=99.0, edge=89.0)], conn=conn) == 0
    stored = conn.execute("SELECT subject_proj, edge FROM recommendation").fetchone()
    assert stored["subject_proj"] == 12.0 and stored["edge"] == 2.0


def test_scoring_fills_in_what_happened(conn):
    scorecard.record("rcl", 2026, 2, [a_row()], conn=conn)
    player_week(conn, "rcl", 2, "11", "Bench Guy", actual=18.0)
    player_week(conn, "rcl", 2, "22", "Starter", actual=6.0)
    assert scorecard.score(conn, 2026, 2) == (1, 0)
    df = scorecard.frame(conn, 2026)
    assert df.loc[0, "gain"] == 12.0
    assert bool(df.loc[0, "right"]) is True


def test_a_call_that_was_wrong_is_recorded_as_wrong(conn):
    """The point of the exercise. A scorecard that cannot say "you were wrong"
    is decoration."""
    scorecard.record("rcl", 2026, 2, [a_row()], conn=conn)
    player_week(conn, "rcl", 2, "11", "Bench Guy", actual=3.0)
    player_week(conn, "rcl", 2, "22", "Starter", actual=14.0)
    scorecard.score(conn, 2026, 2)
    df = scorecard.frame(conn, 2026)
    assert df.loc[0, "gain"] == -11.0
    assert bool(df.loc[0, "right"]) is False


def test_an_unresolvable_call_stays_unscored_rather_than_scoring_zero(conn):
    """A zero is a real football outcome. Inventing one would bias the record
    toward the tool looking worse than it was."""
    scorecard.record("rcl", 2026, 2, [a_row()], conn=conn)
    assert scorecard.score(conn, 2026, 2) == (0, 1)
    assert scorecard.unscored(conn, 2026, 2)


def test_scoring_is_idempotent(conn):
    scorecard.record("rcl", 2026, 2, [a_row()], conn=conn)
    player_week(conn, "rcl", 2, "11", "Bench Guy", actual=18.0)
    player_week(conn, "rcl", 2, "22", "Starter", actual=6.0)
    assert scorecard.score(conn, 2026, 2)[0] == 1
    assert scorecard.score(conn, 2026, 2) == (0, 0)   # nothing left open


def test_a_free_agent_resolves_by_name(conn):
    """A waiver candidate has no row in espn_player_week when the advice is
    given, because nobody rostered him. He shows up once somebody does."""
    scorecard.record("rcl", 2026, 2, [a_row(
        kind="waiver", subject_id="Titans D/ST", subject_name="Titans D/ST",
        against_id="Chiefs D/ST", against_name="Chiefs D/ST")], conn=conn)
    player_week(conn, "rcl", 2, "999", "Titans D/ST", actual=14.0)
    player_week(conn, "rcl", 2, "888", "Chiefs D/ST", actual=5.0)
    assert scorecard.score(conn, 2026, 2) == (1, 0)
    assert scorecard.frame(conn, 2026).loc[0, "gain"] == 9.0


def test_leagues_and_weeks_do_not_collide(conn):
    for league in ("rcl", "dmwd"):
        for week in (2, 3):
            scorecard.record(league, 2026, week, [a_row()], conn=conn)
    assert conn.execute("SELECT count(*) FROM recommendation").fetchone()[0] == 4


def test_summary_reports_hit_rate_and_points(conn):
    scorecard.record("rcl", 2026, 2, [a_row(), a_row(subject_id="33",
                                                     subject_name="Other",
                                                     against_id="44",
                                                     against_name="Other Starter")],
                     conn=conn)
    player_week(conn, "rcl", 2, "11", "Bench Guy", actual=18.0)
    player_week(conn, "rcl", 2, "22", "Starter", actual=6.0)     # right, +12
    player_week(conn, "rcl", 2, "33", "Other", actual=4.0)
    player_week(conn, "rcl", 2, "44", "Other Starter", actual=10.0)   # wrong, -6
    scorecard.score(conn, 2026, 2)
    rows = {r["kind"]: r for r in scorecard.summary(scorecard.frame(conn, 2026))}
    assert rows["start"]["n"] == 2
    assert rows["start"]["right"] == 0.5
    assert rows["start"]["points"] == 6.0
    assert rows["all"]["per_call"] == 3.0


def test_an_empty_scorecard_says_so_rather_than_looking_broken(conn):
    assert "Nothing scored yet" in scorecard.render(scorecard.frame(conn, 2026))


def test_recording_never_takes_the_check_down(monkeypatch):
    """Bookkeeping must not be able to break the thing it is bookkeeping for."""
    from combine import bot

    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(scorecard, "record", boom)
    bot._log_recommendations("rcl", 2, [a_row()])      # must not raise
