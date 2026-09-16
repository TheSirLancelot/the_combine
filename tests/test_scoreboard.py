"""The scoreboard.

Mostly about derived state: whether a game has started, whether it is over, and
which side is mine. Those decide what the page says, and all three are easy to
get subtly wrong when every score is zero on a Sunday morning.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.scoreboard import Game, Side, Unavailable, render


def side(team, score=0.0, proj=120.0, left=9, mine=False):
    return Side(team=team, score=score, projected=proj, yet_to_play=left, mine=mine)


def game(home, away, league="rcl", week=1):
    return Game(league=league, league_name="A League", week=week, home=home, away=away)


def test_a_scoreless_game_is_not_started_not_final():
    g = game(side("H"), side("A"))
    assert not g.started and not g.final


def test_all_players_done_means_final_even_at_zero():
    """A defense can score nothing. Finality comes from players remaining, not
    from the scoreboard being non-zero."""
    g = game(side("H", left=0), side("A", left=0))
    assert g.final and g.started


def test_any_points_means_it_started():
    assert game(side("H", score=4.0), side("A")).started


def test_margin_is_from_my_side_when_i_am_playing():
    g = game(side("H", score=10.0), side("A", score=25.0, mine=True))
    assert g.involves_me
    assert g.me.team == "A" and g.them.team == "H"
    assert g.margin == 15.0


def test_margin_falls_back_to_home_minus_away_when_i_am_not_in_it():
    g = game(side("H", score=10.0), side("A", score=25.0))
    assert not g.involves_me
    assert g.margin == -15.0
    assert g.me is None


def test_projected_margin_uses_the_same_side_as_margin():
    g = game(side("H", proj=100.0), side("A", proj=130.0, mine=True))
    assert g.projected_margin == 30.0


def test_render_marks_my_game_and_explains_its_own_symbols():
    """Unexplained single-character markers in a column are worse than none."""
    text = render([game(side("Mine", score=12.0, mine=True), side("Theirs"))], [])
    assert "*" in text and "your matchup" in text and "F final" in text
    assert "YOU" in text


def test_render_says_why_a_league_is_absent_rather_than_dropping_it():
    text = render([], [Unavailable("work", "League of Degenerates",
                                   "hand-entered, no opponent data")])
    assert "League of Degenerates" in text
    assert "hand-entered" in text


def test_render_survives_having_nothing_at_all():
    assert render([], []) == "no leagues configured"


def test_a_finished_game_reads_as_won_or_lost_not_up_or_down():
    won = render([game(side("Mine", score=120.0, left=0, mine=True),
                       side("Theirs", score=100.0, left=0))], [])
    assert "won by 20.0" in won
    lost = render([game(side("Mine", score=90.0, left=0, mine=True),
                        side("Theirs", score=100.0, left=0))], [])
    assert "lost by 10.0" in lost


# --- the head to head -------------------------------------------------------
#
# The pairing is the whole of the cast view: get it wrong and every row below
# the mistake compares two men who are not in the same slot, which reads as
# perfectly plausible nonsense.

from combine.platforms import ProGame, WeeklyPlayer  # noqa: E402
from combine.pipeline.scoreboard import _cast, _clock, _pair  # noqa: E402


def wp(name, slot, pts=0.0, proj=10.0, played=False, kick=None, status="OK"):
    return WeeklyPlayer(
        player_id=name, name=name, team="SF", pos=slot.split("/")[0], slot=slot,
        status=status, projected=proj, actual=pts, played=played,
        game=ProGame(opponent="KC", home=True, kickoff_ms=kick or 0)
             if kick is not None else None)


def test_pairing_lines_the_two_lineups_up_slot_by_slot():
    rows = _pair([wp("mine-qb", "QB"), wp("mine-rb", "RB")],
                 [wp("his-qb", "QB"), wp("his-rb", "RB")])
    assert [r.slot for r in rows] == ["QB", "RB"]
    assert rows[0].mine.name == "mine-qb" and rows[0].theirs.name == "his-qb"


def test_pairing_follows_my_slot_order_not_his():
    """ESPN hands back the order it prints, and two orders cannot both be the
    one on screen. Mine wins because mine is the lineup being read."""
    rows = _pair([wp("a", "QB"), wp("b", "TE"), wp("c", "RB")],
                 [wp("x", "RB"), wp("y", "QB"), wp("z", "TE")])
    assert [r.slot for r in rows] == ["QB", "TE", "RB"]
    assert [r.theirs.name for r in rows] == ["y", "z", "x"]


def test_a_slot_he_has_and_i_do_not_still_gets_a_row():
    """Dropping it would shift every later row against the wrong man."""
    rows = _pair([wp("a", "QB")], [wp("x", "QB"), wp("y", "K"), wp("z", "K")])
    assert [r.slot for r in rows] == ["QB", "K", "K"]
    assert rows[1].mine is None and rows[1].theirs.name == "y"


def test_an_empty_slot_on_his_side_is_a_row_with_one_man():
    rows = _pair([wp("a", "QB"), wp("b", "RB")], [wp("x", "QB")])
    assert rows[1].mine.name == "b" and rows[1].theirs is None
    assert rows[1].lead == 0.0


def test_lead_is_my_points_minus_his():
    rows = _pair([wp("a", "QB", pts=22.5)], [wp("x", "QB", pts=8.0)])
    assert rows[0].lead == 14.5


def test_duplicate_slots_pair_in_the_order_they_arrive():
    """Two RB rows are not interchangeable: the first of mine faces the first of
    his, which is what the site does and what a screenshot has to match."""
    rows = _pair([wp("mine-1", "RB", pts=5.0), wp("mine-2", "RB", pts=9.0)],
                 [wp("his-1", "RB", pts=1.0), wp("his-2", "RB", pts=2.0)])
    assert [(r.mine.name, r.theirs.name) for r in rows] == [
        ("mine-1", "his-1"), ("mine-2", "his-2")]


def test_the_bench_pairs_by_position_because_every_seat_is_the_same_slot():
    from combine.platforms import Matchup

    m = Matchup(week=1, home_team="H", away_team="A", home_proj=0.0,
                away_proj=0.0,
                home_lineup=[wp("h1", "QB"), wp("h2", "BE"), wp("h3", "BE")],
                away_lineup=[wp("a1", "QB"), wp("a2", "BE")])
    starters, bench = _cast(m, flip=False)
    assert [r.mine.name for r in starters] == ["h1"]
    assert [r.mine.name for r in bench] == ["h2", "h3"]
    assert bench[0].theirs.name == "a2" and bench[1].theirs is None


def test_flip_puts_my_side_on_the_left():
    from combine.platforms import Matchup

    m = Matchup(week=1, home_team="H", away_team="A", home_proj=0.0,
                away_proj=0.0,
                home_lineup=[wp("his", "QB")], away_lineup=[wp("mine", "QB")])
    starters, _ = _cast(m, flip=True)
    assert starters[0].mine.name == "mine" and starters[0].theirs.name == "his"


def test_a_healthy_player_carries_no_status_badge():
    """Otherwise every row on a Sunday morning is wearing a label that says
    nothing, and the ones that matter stop standing out."""
    rows = _pair([wp("a", "QB"), wp("b", "RB", status="Q")], [])
    assert rows[0].mine.status == ""
    assert rows[1].mine.status == "Q"


def test_a_bye_has_no_kickoff():
    rows = _pair([wp("a", "QB")], [])           # no game at all
    assert rows[0].mine.kickoff == "" and rows[0].mine.opponent == ""


def test_the_clock_reads_like_a_clock():
    import time
    from datetime import datetime

    when = datetime(2026, 9, 20, 13, 25)
    assert _clock(int(time.mktime(when.timetuple()) * 1000)) == "Sun 1:25 PM"
    assert _clock(int(time.mktime(
        datetime(2026, 9, 20, 0, 5).timetuple()) * 1000)) == "Sun 12:05 AM"


def test_a_long_name_loses_its_first_name_and_a_short_one_does_not():
    from combine.pipeline.scoreboard import _short

    assert _short("Jahmyr Gibbs") == "J. Gibbs"
    assert _short("Rhamondre Stevenson") == "R. Stevenson"
    assert _short("Brock Purdy") == "Brock Purdy"      # eleven, so it fits
    assert _short("Sam LaPorta") == "Sam LaPorta"
    assert _short("T.J. Watt") == "T.J. Watt"


def test_a_name_with_nothing_to_cut_is_left_alone():
    """A defense is one word, and an initial in place of it would name nobody."""
    from combine.pipeline.scoreboard import _short

    assert _short("49ers D/ST") == "49ers D/ST"
    assert _short("Bengals") == "Bengals"


def test_a_suffix_stays_with_the_surname():
    from combine.pipeline.scoreboard import _short

    assert _short("Travis Etienne Jr.") == "T. Etienne Jr."
