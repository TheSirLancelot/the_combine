"""Win probability by resampling what comparable players actually did.

The properties worth pinning: a bigger projection wins more often, a tie is a
coin flip, spread actually moves the answer, and a player with no comparable
history contributes his projection rather than vanishing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.distribution import Distribution
from combine.pipeline.odds import simulate, win_probability


def history(spread: float, n: int = 400, proj: float = 12.0,
            family: str = "rb") -> Distribution:
    """Rows whose residuals have a known spread, so a test can reason about
    what the simulation should do with them."""
    rng = np.random.default_rng(7)
    resid = rng.normal(0.0, spread, n)
    return Distribution(pd.DataFrame({
        "family": [family] * n,
        "espn_proj": [proj] * n,
        "actual": proj + resid,
        "residual": resid,
    }))


def team(n: int, proj: float, family: str = "rb"):
    return [(family, proj)] * n


def test_the_better_team_wins_more_often_than_not():
    dist = history(6.0)
    p = win_probability(team(5, 14.0), team(5, 10.0), dist)
    assert 0.5 < p < 1.0


def test_two_identical_teams_are_a_coin_flip():
    dist = history(6.0)
    p = win_probability(team(5, 12.0), team(5, 12.0), dist)
    assert abs(p - 0.5) < 0.02


def test_spread_is_what_makes_a_lead_safe():
    """The same four point lead is a very different bet against a quiet
    distribution than a wild one. If this ever stops holding, the simulation
    has stopped reading the history at all."""
    calm = win_probability(team(5, 14.0), team(5, 10.0), history(2.0))
    wild = win_probability(team(5, 14.0), team(5, 10.0), history(14.0))
    assert calm > wild + 0.1


def test_a_player_with_nothing_comparable_still_counts_his_projection():
    """Silently dropping him would quietly hand the matchup to the other side.

    Thirty rows is below the floor the distribution work will quote from, so
    there is nothing to resample and the projection stands on its own.
    """
    rng = np.random.default_rng(1)
    total = simulate([("rb", 20.0)], history(6.0, n=30), 500, rng)
    assert (total == 20.0).all()


def test_an_unfamiliar_position_falls_back_rather_than_going_flat():
    """The ladder is `for_player`'s, and both have to walk the same one or the
    same player gets a spread in one view and none in the other."""
    dist = history(6.0, family="rb")
    assert len(dist.residuals("something-else", 12.0)) > 0
    assert dist.for_player("something-else", 12.0).basis.startswith("all players")


def test_a_player_projected_at_zero_is_left_out_rather_than_resampled():
    """He is on a bye or ruled out. Comparable players did not have that
    problem, so their residuals say nothing about him."""
    dist = history(6.0)
    rng = np.random.default_rng(1)
    total = simulate([("rb", 0.0), ("rb", 0.0)], dist, 200, rng)
    assert (total == 0.0).all()


def test_the_same_question_twice_gives_the_same_answer():
    dist = history(6.0)
    args = (team(5, 13.0), team(5, 12.0), dist)
    assert win_probability(*args) == win_probability(*args)


def test_no_history_at_all_falls_back_to_the_projections():
    """Without a distribution every draw is zero, so the higher projection wins
    every simulated week. Degraded, and visibly so, rather than wrong."""
    assert win_probability(team(5, 13.0), team(5, 12.0), None) == 1.0


def test_two_empty_lineups_are_not_a_division_by_anything():
    assert win_probability([], [], None) == 0.5
