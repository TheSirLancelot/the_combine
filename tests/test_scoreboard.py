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
