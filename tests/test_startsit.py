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


def test_a_near_miss_shows_its_arithmetic():
    """`short_by` folds two things together -- the gap to close and the lead to
    then build -- so on its own it looks wrong. 11.3 against 11.8 reads as half
    a point and the answer is 2.4. The sentence has to show the addition."""
    from combine.pipeline.lineup import NearMiss
    from combine.platforms import WeeklyPlayer

    def wp(name, proj):
        return WeeklyPlayer(player_id=name, name=name, team="KC", pos="WR",
                            slot="WR", projected=proj)

    miss = NearMiss(bench=wp("Jonathon Brooks", 11.3),
                    starter=wp("Courtland Sutton", 11.8), needed=1.9)
    assert round(miss.short_by, 1) == 2.4
    said = miss.explain()
    assert "0.5 behind" in said            # the gap
    assert "lead by 1.9" in said           # the edge on top of it
    assert "so 2.4 more" in said           # and the sum of the two


def test_a_near_miss_that_is_already_ahead_reads_correctly():
    """The other half. Ahead by 0.7 but needing 1.9 is not 'behind by' anything,
    and saying so would be worse than the number it replaced."""
    from combine.pipeline.lineup import NearMiss
    from combine.platforms import WeeklyPlayer

    def wp(name, proj):
        return WeeklyPlayer(player_id=name, name=name, team="KC", pos="WR",
                            slot="WR", projected=proj)

    miss = NearMiss(bench=wp("Ahead Guy", 12.5), starter=wp("Starter", 11.8),
                    needed=1.9)
    said = miss.explain()
    assert "leads" in said and "by 0.7" in said
    assert "behind" not in said
    assert "so 1.2 more" in said


def test_the_displayed_numbers_add_up():
    """A sentence written to show its arithmetic that then fails to add up is
    worse than the bare number it replaced. 2.2 + 1.8 must read as 4.0 even
    when the exact values sum to 4.05."""
    import re

    from combine.pipeline.lineup import NearMiss
    from combine.platforms import WeeklyPlayer

    def wp(name, proj):
        return WeeklyPlayer(player_id=name, name=name, team="KC", pos="WR",
                            slot="WR", projected=proj)

    for bench_proj, starter_proj, needed in ((10.05, 12.28, 1.83),
                                             (11.3, 11.8, 1.9),
                                             (12.5, 11.8, 1.9),
                                             (9.96, 12.31, 1.75)):
        said = NearMiss(bench=wp("B", bench_proj), starter=wp("S", starter_proj),
                        needed=needed).explain()
        behind = "behind" in said
        pattern = r"is (\d+\.\d) behind" if behind else r"by (\d+\.\d) but"
        gap = float(re.search(pattern, said).group(1))
        lead = float(re.search(r"need to lead by (\d+\.\d)", said).group(1))
        total = float(re.search(r"so (\d+\.\d) more", said).group(1))
        assert abs((gap + lead if behind else lead - gap) - total) < 0.001, said
