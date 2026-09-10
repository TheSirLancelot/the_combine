"""The scoring engine.

The reason this module has tests at all is that a name-keyed version of it
validated at 92% on one league and 0% on the other, and looked entirely
reasonable while doing so. Every test here is a bug that actually happened.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.scoring import DEFENSE_IDS, ScoringTable, normalize, resolve


def test_numeric_keys_are_stat_ids_already():
    """Stat lines carry bare numeric keys for stats espn-api has no name for,
    and those are exactly the league-specific scoring buckets. Dropping them
    made every RCL quarterback 11 points light."""
    assert normalize({"8": 10.0}) == {8: 10.0}


def test_named_keys_resolve_to_ids():
    assert normalize({"passingTouchdowns": 2.0}) == {resolve("passingTouchdowns"): 2.0}


def test_ambiguous_names_resolve_to_the_id_the_league_scores():
    """passingYards is both id 3 and id 22. DMWD scores 3, and picking 22
    silently cost every quarterback fifteen points."""
    assert resolve("passingYards", prefer=frozenset({3})) == 3
    assert resolve("passingYards", prefer=frozenset({22})) == 22


def test_ambiguous_names_are_stable_with_nothing_to_prefer():
    """Both sides of a comparison have to agree, so the fallback is fixed
    rather than dependent on dict order."""
    assert resolve("passingYards") == resolve("passingYards") == 3


def test_unpriced_stats_are_ignored_not_fatal():
    """ESPN emits targets, attempts and completion percentage; no league scores
    all of them."""
    table = ScoringTable("t", {3: 0.04})
    assert table.score({"passingYards": 250.0, "receivingTargets": 9.0}) == 10.0


def test_scoring_is_points_per_unit():
    table = ScoringTable("t", {resolve("passingCompletions"): 1.0, 3: 0.04})
    got = table.score({"passingCompletions": 24.0, "passingYards": 275.0})
    assert got == 24.0 + 11.0


def test_defensive_stats_are_not_paid_to_a_receiver_without_idp_slots():
    """ESPN projects defensive stats for two-way players. Travis Hunter is the
    case: in a league with only a team D/ST slot, ESPN pays his projected
    interceptions to nobody."""
    stat = next(iter(DEFENSE_IDS & {95}))
    table = ScoringTable("no-idp", {stat: 2.0, 3: 0.04}, idp=False)
    assert table.score({str(stat): 1.0}, position="WR") == 0.0
    assert table.score({str(stat): 1.0}, position="D/ST") == 2.0


def test_defensive_stats_do_pay_individuals_in_an_idp_league():
    table = ScoringTable("idp", {95: 2.0}, idp=True)
    assert table.score({"95": 1.0}, position="CB") == 2.0


def test_return_touchdowns_are_not_treated_as_defensive():
    """Kick and punt return TDs sit in the same id band as defensive stats but a
    returner earns them, and returners are receivers and backs. Excluding them
    made every return man project low."""
    for stat in (101, 102, 93):
        assert stat not in DEFENSE_IDS
        table = ScoringTable("no-idp", {stat: 6.0}, idp=False)
        assert table.score({str(stat): 1.0}, position="WR") == 6.0


def test_position_unknown_means_score_everything():
    """Callers without a position should not silently lose stats."""
    table = ScoringTable("no-idp", {95: 2.0}, idp=False)
    assert table.score({"95": 1.0}) == 2.0
