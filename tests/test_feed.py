"""The live feed.

Impossible to watch happen out of season, so these build the one thing the feed
actually depends on: two reads of the same players with different numbers in
them. That is the whole mechanism. If subtracting the first from the second
produces the right sentence, the feed works on a Sunday.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import db
from combine.pipeline.feed import EPSILON, Event, observe, recent, render
from combine.pipeline.scoreboard import Cell, Game, Row, Side

SEASON = 2026


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "feed.db"
    db.ensure_schema(path)
    with db.connect(path) as c:
        yield c


def cell(espn_id, name, points, counts=()):
    return Cell(espn_id=espn_id, name=name, short=name, pos="RB", team="DET",
                opponent="@ BUF", kickoff="Sun 1:00 PM", status="", line="",
                points=points, projected=15.0, played=True, locked=True,
                counts=tuple(sorted(counts)))


def game(rows, mine="Mine", theirs="Theirs", week=2):
    return Game(league="rcl", league_name="RCL", week=week,
                home=Side(team=mine, score=0.0, projected=0.0, yet_to_play=0,
                          mine=True),
                away=Side(team=theirs, score=0.0, projected=0.0, yet_to_play=0,
                          mine=False),
                starters=tuple(rows))


def test_a_first_read_produces_nothing(conn):
    g = game([Row("RB", cell("1", "Gibbs", 12.4), None)])
    assert observe(conn, [g], SEASON, "2026-09-20T18:00:00+00:00") == []


def test_the_second_read_reports_only_what_moved(conn):
    before = {"rushingAttempts": 11.0, "rushingYards": 44.0}
    after = {"rushingAttempts": 12.0, "rushingYards": 56.0,
             "rushingTouchdowns": 1.0}

    observe(conn, [game([Row("RB", cell("1", "Gibbs", 12.4, before.items()),
                             None)])], SEASON, "2026-09-20T18:00:00+00:00")
    out = observe(conn, [game([Row("RB", cell("1", "Gibbs", 19.6, after.items()),
                                   None)])], SEASON, "2026-09-20T18:00:30+00:00")

    assert len(out) == 1
    e = out[0]
    assert e.points == 7.2                   # what this read added
    assert e.total == 19.6                   # where he stands now
    assert e.what == "1 car, 12 yd, 1 TD"    # and only what changed


def test_a_player_who_did_not_move_says_nothing(conn):
    same = {"rushingAttempts": 11.0, "rushingYards": 44.0}
    rows = [Row("RB", cell("1", "Gibbs", 12.4, same.items()), None)]
    observe(conn, [game(rows)], SEASON, "2026-09-20T18:00:00+00:00")
    assert observe(conn, [game(rows)], SEASON, "2026-09-20T18:00:30+00:00") == []


def test_float_dust_is_not_a_play(conn):
    """ESPN rounds to a tenth; json round-trips do not. A read that differs in
    the twelfth decimal place is the same read."""
    rows = [Row("RB", cell("1", "Gibbs", 12.4), None)]
    observe(conn, [game(rows)], SEASON, "2026-09-20T18:00:00+00:00")
    nudge = [Row("RB", cell("1", "Gibbs", 12.4 + EPSILON / 2), None)]
    assert observe(conn, [game(nudge)], SEASON, "2026-09-20T18:00:30+00:00") == []


def test_a_stat_correction_going_backwards_is_still_reported(conn):
    """It changes the score, so hiding it would leave the scoreboard and the
    feed disagreeing with no explanation on offer."""
    observe(conn, [game([Row("RB", cell("1", "Gibbs", 19.6), None)])],
            SEASON, "2026-09-20T18:00:00+00:00")
    out = observe(conn, [game([Row("RB", cell("1", "Gibbs", 13.6), None)])],
                  SEASON, "2026-09-20T20:00:00+00:00")
    assert out[0].points == -6.0
    assert not out[0].scored


def test_both_sides_of_my_matchup_are_watched(conn):
    """His opponent scoring is the same news as his own player scoring, and a
    feed that only shows one of them answers half the question."""
    rows = [Row("RB", cell("1", "Mine", 0.0), cell("2", "His", 0.0))]
    observe(conn, [game(rows)], SEASON, "2026-09-20T18:00:00+00:00")
    moved = [Row("RB", cell("1", "Mine", 6.0), cell("2", "His", 9.0))]
    out = observe(conn, [game(moved)], SEASON, "2026-09-20T18:00:30+00:00")
    assert {e.mine for e in out} == {True, False}
    assert {e.side for e in out} == {"Mine", "Theirs"}


def test_a_game_i_am_not_in_is_not_watched(conn):
    """The scoreboard already drops them. Recording them would fill the feed
    with a league he has said he does not follow."""
    other = Game(league="rcl", league_name="RCL", week=2,
                 home=Side(team="A", score=0.0, projected=0.0, yet_to_play=0,
                           mine=False),
                 away=Side(team="B", score=0.0, projected=0.0, yet_to_play=0,
                           mine=False),
                 starters=(Row("RB", cell("9", "Nobody", 0.0), None),))
    assert observe(conn, [other], SEASON, "2026-09-20T18:00:00+00:00") == []
    assert recent(conn, SEASON, 2) == []


def test_points_that_moved_with_nothing_nameable_still_count(conn):
    """A scoring setting we have no word for still changes the score. The event
    is real; only the description is missing, and it says so rather than
    inventing one."""
    observe(conn, [game([Row("RB", cell("1", "X", 0.0, {"999": 1.0}.items()),
                             None)])], SEASON, "2026-09-20T18:00:00+00:00")
    out = observe(conn, [game([Row("RB", cell("1", "X", 4.0,
                                              {"999": 2.0}.items()), None)])],
                  SEASON, "2026-09-20T18:00:30+00:00")
    assert out[0].points == 4.0
    assert out[0].what == ""


def test_recent_is_newest_first(conn):
    for n, pts in enumerate([0.0, 3.0, 9.0, 14.0]):
        observe(conn, [game([Row("RB", cell("1", "Gibbs", pts), None)])],
                SEASON, f"2026-09-20T18:0{n}:00+00:00")
    got = recent(conn, SEASON, 2)
    assert [e.total for e in got] == [14.0, 9.0, 3.0]


def test_recent_does_not_bleed_between_weeks(conn):
    observe(conn, [game([Row("RB", cell("1", "G", 0.0), None)], week=2)],
            SEASON, "2026-09-20T18:00:00+00:00")
    observe(conn, [game([Row("RB", cell("1", "G", 8.0), None)], week=2)],
            SEASON, "2026-09-20T18:01:00+00:00")
    assert len(recent(conn, SEASON, 2)) == 1
    assert recent(conn, SEASON, 3) == []


def test_the_clock_is_local_and_reads_like_one():
    e = Event(league="rcl", week=2, espn_id="1", name="G", short="G", pos="RB",
              team="DET", slot="RB", side="Mine", mine=True, points=6.0,
              total=6.0, what="", at="2026-09-20T18:42:00+00:00")
    assert e.clock.endswith(("AM", "PM"))
    assert ":" in e.clock


def test_an_empty_feed_says_why_rather_than_nothing():
    """A blank panel reads as a quiet afternoon. It is not: it means this has
    only just started watching."""
    assert "difference between reads" in render([])
