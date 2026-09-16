"""Comparing any two players, and pricing the swap.

The old head-to-head only saw this week's matchup, so it could not answer the
comparison that usually matters: your man against somebody else's, or against
one nobody has.

Two properties this must keep. The swap is priced on the WHOLE lineup, because
a player who frees a slot is worth more than his projection says. And
opportunities are never subtracted across positions, because targets and
touches are different units.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.compare import FREE, MINE, compare, resolve
from combine.platforms import Matchup, ProGame, WeeklyPlayer

FUTURE = 4_000_000_000_000


def player(name, pos, slot="BE", proj=10.0, eligible=None):
    return WeeklyPlayer(
        player_id=name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset(eligible or {pos, "BE"}), projected=proj,
        game=ProGame(opponent="SF", home=True, kickoff_ms=FUTURE))


class PoolPlayer:
    def __init__(self, name, pos, proj, season=100.0):
        self.name = name
        self.position = pos
        self.proTeam = "KC"
        self.playerId = name
        self.injuryStatus = "ACTIVE"
        self.eligibleSlots = [pos, "BE"]
        self.projected_total_points = season
        self.stats = {1: {"projected_points": proj}}


class Client:
    week = 1
    cfg = type("C", (), {"team_id": 6})()

    def __init__(self, mine, others=(), pool=()):
        self.mine = list(mine)
        self.others = list(others)
        self.league = type("L", (), {
            "free_agents": staticmethod(lambda size=400: list(pool)),
        })()

    def roster_slots(self):
        return {"RB": 1, "WR": 1}

    def matchup(self, week=None):
        return Matchup(week=1, home_team="Me", away_team="Them", home_proj=0.0,
                       away_proj=0.0, home_lineup=self.mine, away_lineup=[],
                       mine="home")

    def pro_schedule(self, week=None):
        return {}

    def player_weeks(self, week):
        return ([("Mine", "Them", p) for p in self.mine]
                + [("Rival Team", "Them", p) for p in self.others])


def test_it_finds_players_on_any_roster_and_in_the_pool():
    client = Client([player("Mine Guy", "RB", slot="RB")],
                    others=[player("Their Guy", "WR", slot="WR")],
                    pool=[PoolPlayer("Free Guy", "WR", 9.0)])
    index_owners = {}
    for name in ("Mine Guy", "Their Guy", "Free Guy"):
        result, err = compare(client, name, "Mine Guy" if name != "Mine Guy"
                              else "Their Guy")
        assert not err, err
        side = result.a if result.a.player.name == name else result.b
        index_owners[name] = side.owner
    assert index_owners["Mine Guy"] == MINE
    assert index_owners["Their Guy"] == "Rival Team"
    assert index_owners["Free Guy"] == FREE


def test_a_cross_position_pair_is_allowed():
    """The whole point of the request: any two players, regardless of
    position."""
    client = Client([player("My RB", "RB", slot="RB", proj=18.0)],
                    others=[player("Their WR", "WR", slot="WR", proj=12.0)])
    result, err = compare(client, "My RB", "Their WR")
    assert not err
    assert result.swappable


def test_the_swap_is_priced_on_the_whole_lineup():
    """Not the pair. Swapping a 18 point back for a 12 point receiver costs
    more than six points if the receiver cannot fill the back's slot."""
    mine = [player("My RB", "RB", slot="RB", proj=18.0),
            player("My WR", "WR", slot="WR", proj=10.0)]
    client = Client(mine, others=[player("Their WR", "WR", slot="WR", proj=12.0)])
    result, _ = compare(client, "My RB", "Their WR")
    assert result.now == 28.0            # 18 + 10
    assert result.swapped == 12.0        # RB slot empty, best WR starts
    assert result.delta == -16.0


def test_a_swap_that_helps_reads_positive():
    mine = [player("Weak RB", "RB", slot="RB", proj=4.0),
            player("My WR", "WR", slot="WR", proj=10.0)]
    client = Client(mine, pool=[PoolPlayer("Strong RB", "RB", 15.0)])
    result, _ = compare(client, "Weak RB", "Strong RB")
    assert result.delta == 11.0
    assert result.incoming.player.name == "Strong RB"
    assert result.outgoing.player.name == "Weak RB"


def test_two_of_your_own_players_have_no_swap_to_price():
    """That is a lineup question, and the optimizer already answers it."""
    mine = [player("A", "RB", slot="RB"), player("B", "WR", slot="WR")]
    result, _ = compare(Client(mine), "A", "B")
    assert result.swappable is False


def test_two_players_who_are_both_somebody_else_s_have_no_swap_either():
    client = Client([player("Mine", "RB", slot="RB")],
                    others=[player("X", "WR", slot="WR")],
                    pool=[PoolPlayer("Y", "WR", 9.0)])
    result, _ = compare(client, "X", "Y")
    assert result.swappable is False


def test_an_ambiguous_name_lists_the_matches():
    client = Client([player("Josh Allen", "QB", slot="RB"),
                     player("Josh Downs", "WR", slot="WR")])
    result, err = compare(client, "Josh", "Josh Downs")
    assert result is None
    assert "matches 2" in err and "Josh Allen" in err


def test_an_exact_name_beats_a_substring():
    """'Josh Downs' must not be ambiguous just because 'Josh Downs Jr.'
    exists."""
    side, err = resolve(
        {"josh downs": "exact", "josh downs jr.": "other"}, "Josh Downs")
    assert side == "exact" and not err


def test_an_unknown_name_says_so():
    client = Client([player("Mine", "RB", slot="RB")])
    result, err = compare(client, "Nobody At All", "Mine")
    assert result is None
    assert "not in this league" in err


def test_the_same_player_twice_is_refused():
    client = Client([player("Mine", "RB", slot="RB")])
    result, err = compare(client, "Mine", "Mine")
    assert result is None
    assert "same player" in err
