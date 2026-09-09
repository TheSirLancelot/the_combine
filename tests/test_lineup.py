"""Pure-function tests for the weekly lineup view.

No network. The live ESPN path is exercised by `combine week <league>`; these
cover the slot logic that start/sit will be built on top of, which is the part
that will get quietly wrong when the optimizer lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.lineup import order_starters, problems, split, swaps
from combine.platforms import WeeklyPlayer


def p(name, pos, slot, proj, elig=(), status="OK", bye=False) -> WeeklyPlayer:
    return WeeklyPlayer(
        player_id=name, name=name, team="XX", pos=pos, slot=slot,
        eligible_slots=frozenset(elig or (pos, "BE")), status=status,
        projected=proj, on_bye=bye,
    )


def test_split_puts_bench_in_projection_order():
    lineup = [p("a", "RB", "RB", 10), p("b", "RB", "BE", 5), p("c", "WR", "BE", 9)]
    starters, bench = split(lineup)
    assert [x.name for x in starters] == ["a"]
    assert [x.name for x in bench] == ["c", "b"]


def test_ir_counts_as_bench():
    _, bench = split([p("hurt", "RB", "IR", 0)])
    assert [x.name for x in bench] == ["hurt"]


def test_order_starters_follows_league_slot_order():
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}
    lineup = [p("wr", "WR", "WR", 8), p("qb", "QB", "QB", 20), p("rb", "RB", "RB", 12)]
    assert [x.name for x in order_starters(lineup, slots)] == ["qb", "rb", "wr"]


def test_unknown_slot_sorts_last_not_first():
    """An IDP or flex slot the league dict does not name must not jump the queue."""
    slots = {"QB": 1, "RB": 2}
    lineup = [p("flex", "RB", "RB/WR/TE", 15), p("qb", "QB", "QB", 20)]
    assert [x.name for x in order_starters(lineup, slots)] == ["qb", "flex"]


def test_swap_requires_slot_eligibility():
    """A WR outprojecting the QB is not a swap. This is the whole point of
    carrying eligible_slots through from the box score."""
    starters = [p("qb", "QB", "QB", 10)]
    bench = [p("wr", "WR", "BE", 25, elig=("WR", "BE"))]
    assert swaps(starters, bench) == []


def test_swap_found_when_eligible_and_ahead():
    starters = [p("weak", "RB", "RB", 6)]
    bench = [p("strong", "RB", "BE", 14, elig=("RB", "BE"))]
    got = swaps(starters, bench)
    assert len(got) == 1 and got[0].bench.name == "strong" and got[0].edge == 8


def test_swap_ignores_edges_inside_the_noise():
    starters = [p("weak", "RB", "RB", 10)]
    bench = [p("barely", "RB", "BE", 10.4, elig=("RB", "BE"))]
    assert swaps(starters, bench) == []


def test_each_starter_is_offered_once():
    """Three bench players all beating the same weak starter should produce one
    hint, not three near-identical ones."""
    starters = [p("weak", "RB", "RB", 4), p("ok", "RB", "RB", 30)]
    bench = [p(n, "RB", "BE", v, elig=("RB", "BE")) for n, v in
             (("b1", 15), ("b2", 14), ("b3", 13))]
    got = swaps(starters, bench)
    assert len(got) == 1 and got[0].bench.name == "b1" and got[0].starter.name == "weak"


def test_swap_targets_the_worst_eligible_starter():
    starters = [p("mid", "RB", "RB", 12), p("worst", "RB", "RB", 5)]
    bench = [p("up", "RB", "BE", 14, elig=("RB", "BE"))]
    assert swaps(starters, bench)[0].starter.name == "worst"


def test_hurt_or_bye_bench_player_is_never_suggested():
    starters = [p("weak", "RB", "RB", 4)]
    bench = [p("out", "RB", "BE", 20, elig=("RB", "BE"), status="O"),
             p("resting", "RB", "BE", 18, elig=("RB", "BE"), bye=True)]
    assert swaps(starters, bench) == []


def test_problem_starters_catch_status_and_bye():
    """ESPN keeps projecting players it has already marked OUT, so status has
    to beat projection here."""
    starters = [p("fine", "RB", "RB", 12), p("out", "WR", "WR", 14, status="O"),
                p("bye", "TE", "TE", 9, bye=True), p("quest", "QB", "QB", 20, status="Q")]
    assert {x.name for x in problems(starters)} == {"out", "bye"}
