"""Measuring what the waiver wire was worth.

The arithmetic here is simple; the honesty is the hard part. Every property is
about not overclaiming, so the tests are mostly about what the numbers do NOT
mean.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.wire import FORM_MIN_WEEKS, WireWeek, _form, form_ranking


def week(**kw):
    base = dict(league="rcl", week=5, fielded=100.0, best_lineup_value=110.0,
                with_best_add=130.0, add_name="A", add_pos="WR", drop_name="D",
                opponent_actual=115.0, with_form_add=118.0, form_add_name="F")
    base.update(kw)
    return WireWeek(**base)


def test_form_uses_only_earlier_weeks():
    """The leakage rule. Form for week 5 cannot see week 5."""
    by_week = {1: 10.0, 2: 10.0, 3: 10.0, 5: 99.0}
    assert _form(by_week, 5) == 10.0


def test_form_needs_a_minimum_history():
    assert _form({1: 10.0}, 5) is None
    assert _form({1: 10.0, 2: 20.0}, 5) == 15.0
    assert FORM_MIN_WEEKS == 2


def test_form_is_a_trailing_window_not_the_whole_season():
    """A player who was good in September and bad since should read as bad."""
    by_week = {1: 30.0, 2: 30.0, 3: 30.0, 4: 2.0, 5: 2.0, 6: 2.0}
    assert _form(by_week, 7) == 2.0


def test_wire_value_is_isolated_from_lineup_mistakes():
    """Both sides use hindsight, so this measures the ADD and not the failure to
    set a lineup properly."""
    row = week(fielded=80.0, best_lineup_value=110.0, with_best_add=130.0)
    assert row.wire_value == 20.0


def test_form_value_can_be_zero_when_the_pick_does_not_help():
    row = week(best_lineup_value=110.0, with_form_add=110.0)
    assert row.form_value == 0.0


def test_flipped_needs_the_add_to_cross_the_opponent_not_just_gain():
    """Gaining 20 points in a game you lost by 40 changed nothing."""
    lost_anyway = week(best_lineup_value=60.0, with_form_add=80.0,
                       opponent_actual=115.0)
    assert lost_anyway.form_value == 20.0
    assert not lost_anyway.form_flipped

    genuine = week(best_lineup_value=110.0, with_form_add=118.0,
                   opponent_actual=115.0)
    assert genuine.form_flipped


def test_already_winning_is_not_a_flip():
    """Adding points to a game you already had won is not a changed result."""
    row = week(best_lineup_value=120.0, with_form_add=140.0, opponent_actual=115.0)
    assert not row.form_flipped


def test_form_ranking_is_ordered_and_capped():
    actuals = {str(i): {1: 5.0, 2: float(i), 3: float(i)} for i in range(1, 60)}
    ranked = form_ranking(actuals, range(3, 4))[3]
    assert ranked[0][1] > ranked[-1][1]
    assert len(ranked) <= 40


def test_form_ranking_skips_players_without_enough_history():
    actuals = {"thin": {2: 30.0}, "ok": {1: 5.0, 2: 5.0}}
    ranked = dict(form_ranking(actuals, range(3, 4))[3])
    assert "thin" not in ranked and "ok" in ranked
