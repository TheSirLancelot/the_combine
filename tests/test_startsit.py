"""Start/sit verdicts.

No network. The thing worth guarding is that usage only ever confirms or
contradicts the projection inside a position family, and never gets averaged
into it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.startsit import _verdict, opportunity_edge
from combine.pipeline.usage import Usage, family
from combine.platforms import ProGame, WeeklyPlayer

FUTURE = 4_000_000_000_000


def wp(pid, name, pos, slot="BE", proj=10.0) -> WeeklyPlayer:
    return WeeklyPlayer(player_id=pid, name=name, team="KC", pos=pos, slot=slot,
                        eligible_slots=frozenset({pos, "BE"}), projected=proj,
                        game=ProGame(opponent="SF", home=True, kickoff_ms=FUTURE))


def wr_usage(pff_id, routes, targets, games=17) -> Usage:
    return Usage(pff_id=pff_id, name="x", season=2025, rows={"receiving": {
        "player_game_count": games, "routes": routes, "targets": targets,
        "route_rate": 90.0, "yprr": 1.8, "grades_pass_route": 75.0}})


def rb_usage(pff_id, attempts, receptions, games=17) -> Usage:
    return Usage(pff_id=pff_id, name="x", season=2025, rows={"rushing": {
        "player_game_count": games, "attempts": attempts, "receptions": receptions,
        "grades_offense": 75.0}})


def test_families_split_the_units():
    assert family("WR") == family("TE") == "pass-catcher"
    assert family("RB") != family("WR")
    assert family("QB") == "qb"
    assert family("LB") == family("CB") == "idp"
    # K and D/ST were one "other" family and should not have been: a kicker's
    # outcomes spread 11.4 points p10 to p90 against a defense's 17.0, so a
    # threshold averaged over the two is wrong for both.
    assert family("D/ST") == "dst"
    assert family("K") == "k"
    assert family("D/ST") != family("K")


def test_opportunity_edge_refuses_to_cross_positions():
    """A tight end's targets minus a running back's touches is not a number.
    Same class of mistake as ranking the board on points against ADP."""
    usage = {1: wr_usage(1, routes=510, targets=95), 2: rb_usage(2, 270, 40)}
    ids = {"a": 1, "b": 2}
    edge, why = opportunity_edge(wp("a", "A", "TE"), wp("b", "B", "RB"), usage, ids)
    assert edge is None and "not comparable" in why


def test_opportunity_edge_inside_a_family():
    usage = {1: wr_usage(1, 510, 119), 2: wr_usage(2, 300, 51)}
    edge, why = opportunity_edge(wp("a", "A", "WR"), wp("b", "B", "WR"),
                                 usage, {"a": 1, "b": 2})
    assert why == ""
    assert round(edge, 1) == 4.0   # 7.0 targets a game against 3.0


def test_missing_usage_is_not_a_zero():
    """An unresolved player must not read as a player with no opportunities."""
    usage = {1: wr_usage(1, 510, 119)}
    edge, why = opportunity_edge(wp("a", "A", "WR"), wp("b", "B", "WR"),
                                 usage, {"a": 1})
    assert edge is None and "no PFF usage" in why


def test_clear_needs_both_the_gap_and_the_usage():
    assert _verdict(5.0, 3.0)[0] == "CLEAR"
    assert _verdict(1.5, 3.0)[0] == "LEAN"


def test_disagreement_is_a_coin_flip_not_an_average():
    verdict, reasons = _verdict(6.0, -4.0)
    assert verdict == "COIN FLIP"
    assert "disagree" in reasons[0]


def test_no_usage_downgrades_confidence():
    assert _verdict(6.0, None)[0] == "LEAN"
    assert _verdict(1.5, None)[0] == "COIN FLIP"


def test_small_sample_is_called_out():
    assert any("4 games" in c for c in wr_usage(1, 120, 30, games=4).caveats(True))


def test_preseason_usage_is_labelled_as_a_prior():
    notes = wr_usage(1, 510, 119).caveats(in_season=False)
    assert any("prior" in n for n in notes)
    assert wr_usage(1, 510, 119).caveats(in_season=True) == []
