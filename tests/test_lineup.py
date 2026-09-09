"""Pure-function tests for the weekly lineup view.

No network. The live ESPN path is exercised by `combine week <league>`; these
cover the slot logic that start/sit will be built on top of, which is the part
that will get quietly wrong when the optimizer lands.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.lineup import effective, order_starters, problems, split, swaps
from combine.platforms import ProGame, WeeklyPlayer

# Kickoff times either side of "now", so `locked` is deterministic in tests.
PAST = 1_000_000_000_000
FUTURE = 4_000_000_000_000


def p(name, pos, slot, proj, elig=(), status="OK", bye=False,
      kickoff=FUTURE) -> WeeklyPlayer:
    return WeeklyPlayer(
        player_id=name, name=name, team="XX", pos=pos, slot=slot,
        eligible_slots=frozenset(elig or (pos, "BE")), status=status,
        game=None if bye else ProGame(opponent="YY", home=True, kickoff_ms=kickoff),
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


def test_opponent_label_reads_home_and_away():
    home = WeeklyPlayer(player_id="1", name="h", team="XX", pos="RB", slot="RB",
                        game=ProGame(opponent="KC", home=True, kickoff_ms=FUTURE))
    away = WeeklyPlayer(player_id="2", name="a", team="XX", pos="RB", slot="RB",
                        game=ProGame(opponent="KC", home=False, kickoff_ms=FUTURE))
    assert home.opponent == "vs KC"
    assert away.opponent == "@ KC"


def test_bye_player_has_no_game_and_says_so():
    """Bye is derived from the schedule: no game for his NFL team that week."""
    resting = p("resting", "RB", "BE", 0, bye=True)
    assert resting.game is None and resting.opponent == "BYE"


def test_locked_follows_kickoff():
    assert p("started", "RB", "RB", 10, kickoff=PAST).locked
    assert not p("later", "RB", "RB", 10, kickoff=FUTURE).locked


def test_locked_starter_is_not_offered_for_a_swap():
    """His game kicked off, so the decision is already made and a hint is noise."""
    starters = [p("playing", "RB", "RB", 4, kickoff=PAST)]
    bench = [p("better", "RB", "BE", 20, elig=("RB", "BE"))]
    assert swaps(starters, bench) == []


def test_locked_bench_player_is_never_suggested():
    starters = [p("weak", "RB", "RB", 4)]
    bench = [p("already-played", "RB", "BE", 20, elig=("RB", "BE"), kickoff=PAST)]
    assert swaps(starters, bench) == []


def test_a_ruled_out_starter_is_worth_nothing():
    """ESPN still projects 11 points for a player it has marked OUT, which is
    enough to beat every healthy bench player and suppress the one swap that
    actually matters."""
    assert effective(p("out", "RB", "RB", 11, status="O")) == 0
    assert effective(p("bye", "RB", "RB", 11, bye=True)) == 0
    assert effective(p("fine", "RB", "RB", 11)) == 11


def test_a_healthy_bench_player_replaces_a_ruled_out_starter():
    starters = [p("out", "RB", "RB", 11, status="O")]
    bench = [p("healthy", "RB", "BE", 7, elig=("RB", "BE"))]
    got = swaps(starters, bench)
    assert len(got) == 1 and got[0].bench.name == "healthy" and got[0].edge == 7


def test_the_ruled_out_starter_is_preferred_over_a_merely_weak_one():
    starters = [p("weak", "RB", "RB", 5), p("out", "RB", "RB", 14, status="O")]
    bench = [p("healthy", "RB", "BE", 9, elig=("RB", "BE"))]
    assert swaps(starters, bench)[0].starter.name == "out"


# --- optimal assignment ---------------------------------------------------

def test_optimizer_finds_the_move_a_pairwise_check_cannot():
    """Put the receiver in the flex so the second back can take the RB slot.
    No single swap gets there, which is the whole reason for an exact solver."""
    from combine.pipeline.optimize import best_lineup
    def c(name, pos, elig, proj):
        return {"espn_id": name, "name": name, "pos": pos,
                "eligible": set(elig), "proj": proj, "playable": True}
    roster = [c("RB1", "RB", ["RB", "FLEX"], 14), c("RB2", "RB", ["RB", "FLEX"], 11),
              c("WR1", "WR", ["WR", "FLEX"], 13), c("WR2", "WR", ["WR", "FLEX"], 9)]
    got = best_lineup(roster, ["RB", "WR", "FLEX"], key=lambda p: p["proj"])
    assert {p["name"] for p in got} == {"RB1", "WR1", "RB2"}
    assert sum(p["proj"] for p in got) == 38


def test_optimizer_will_not_fill_a_slot_illegally():
    from combine.pipeline.optimize import best_lineup
    kicker = {"espn_id": "k", "pos": "K", "eligible": {"K"}, "proj": 9.0,
              "playable": True}
    assert best_lineup([kicker], ["QB"], key=lambda p: p["proj"]) == []


def test_optimizer_leaves_an_already_correct_lineup_alone():
    from combine.pipeline.lineup import optimal_moves
    lineup = [p("rb", "RB", "RB", 14), p("wr", "WR", "WR", 12),
              p("sub", "WR", "BE", 5, elig=("WR", "BE"))]
    add, drop, gain = optimal_moves(lineup, {"RB": 1, "WR": 1})
    assert add == [] and drop == [] and gain == 0


def test_optimizer_benches_a_ruled_out_starter_for_anyone_healthy():
    from combine.pipeline.lineup import optimal_moves
    lineup = [p("out", "RB", "RB", 16, status="O"),
              p("fit", "RB", "BE", 6, elig=("RB", "BE"))]
    add, drop, gain = optimal_moves(lineup, {"RB": 1})
    assert [x.name for x in add] == ["fit"]
    assert [x.name for x in drop] == ["out"]
    assert gain == 6


def test_optimizer_does_not_move_a_player_whose_game_started():
    """A suggestion you cannot act on is noise."""
    from combine.pipeline.lineup import optimal_moves
    lineup = [p("playing", "RB", "RB", 4, kickoff=PAST),
              p("better", "RB", "BE", 15, elig=("RB", "BE"))]
    add, drop, _gain = optimal_moves(lineup, {"RB": 1})
    assert add == [] and drop == []


# --- how big a gap has to be before it is worth reading -------------------

class FakeDist:
    """Stands in for the outcome history. Only `spread` matters here."""

    def __init__(self, spread):
        self.spread = spread

    def for_player(self, family, proj):
        from combine.pipeline.distribution import Band
        return Band(floor=0, median=proj, ceiling=0, boom=0, bust=0,
                    spread=self.spread, n=999, basis="fake")


def test_required_edge_scales_with_how_noisy_the_outcomes_are():
    """Two points means more between two defenders than between two backs
    projected 20+, whose outcomes scatter nearly twice as widely."""
    from combine.pipeline.lineup import MIN_Z, required_edge
    quiet, noisy = p("a", "LB", "LB", 10), p("b", "LB", "BE", 12)
    assert required_edge(quiet, noisy, FakeDist(6.0)) == MIN_Z * 6.0
    assert required_edge(quiet, noisy, FakeDist(10.6)) == MIN_Z * 10.6


def test_required_edge_never_drops_below_the_floor():
    """A gap under a point is inside the rounding of the projections."""
    from combine.pipeline.lineup import MIN_EDGE, required_edge
    a, b = p("a", "WR", "WR", 4), p("b", "WR", "BE", 5)
    assert required_edge(a, b, FakeDist(1.0)) == MIN_EDGE


def test_required_edge_falls_back_when_there_is_no_history():
    from combine.pipeline.lineup import MIN_EDGE, required_edge
    a, b = p("a", "WR", "WR", 10), p("b", "WR", "BE", 12)
    assert required_edge(a, b, None) == MIN_EDGE


def test_a_noisy_pair_needs_a_bigger_gap_to_be_flagged():
    """The same 2 point gap is worth surfacing between two quiet players and
    not between two noisy ones."""
    starters = [p("starter", "RB", "RB", 10)]
    bench = [p("bench", "RB", "BE", 12, elig=("RB", "BE"))]
    assert len(swaps(starters, bench, dist=FakeDist(6.0))) == 1     # needs 1.5
    assert swaps(starters, bench, dist=FakeDist(10.6)) == []        # needs 2.65


def test_required_edge_survives_a_band_without_spread():
    """Regression: a Band built by an older version of the module, kept alive
    in a Streamlit cache across a code reload, reached new code that expected a
    field it did not have. The threshold must degrade to the floor, never
    raise, whatever a caller hands it."""
    from combine.pipeline.lineup import MIN_EDGE, required_edge

    class Old:
        """A Band as it looked before `spread` existed."""
        floor = 3.0
        ceiling = 20.0

    class OldDist:
        def for_player(self, family, proj):
            return Old()

    a, b = p("a", "WR", "WR", 10), p("b", "WR", "BE", 13)
    assert required_edge(a, b, OldDist()) == MIN_EDGE
    assert len(swaps([a], [b], dist=OldDist())) == 1
