"""Roster depth: who the wire beats at his own position.

This exists because the weekly waiver view answered correctly and uselessly.
DMWD carried De'Zhaun Stribling at 108 rest-of-season points while Kenyon Sadiq
sat unrostered at 147, and the weekly view had nothing to say, because neither
man was going to start on Sunday.

The two things it must not do are what these mostly test. It must not compare
points across positions, which would recommend six backup quarterbacks. And it
must not recommend dropping a player being deliberately kept.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.depth import find
from combine.platforms import ProGame, WeeklyPlayer


def player(name, pos, slot="BE", status="OK"):
    return WeeklyPlayer(
        player_id=name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset({pos, "BE"}), projected=5.0, status=status,
        game=ProGame(opponent="SF", home=True, kickoff_ms=4_000_000_000_000))


class PoolPlayer:
    def __init__(self, name, pos, season):
        self.name = name
        self.position = pos
        self.playerId = name
        self.projected_total_points = season


class Client:
    cfg = type("C", (), {"team_id": 6})()

    def __init__(self, pool):
        self.league = type("L", (), {
            "free_agents": staticmethod(lambda size=350: pool),
            "transactions": staticmethod(lambda types=None: []),
        })()


def test_it_finds_a_bench_player_the_wire_beats():
    """The case it was built for. Nothing about it changes Sunday."""
    lineup = [player("Stribling", "WR")]
    client = Client([PoolPlayer("Sadiq", "WR", 147.0)])
    gaps = find(client, {"Stribling": 108.0}, lineup, set())
    assert [g.name for g in gaps] == ["Stribling"]
    assert gaps[0].gain == 39.0


def test_points_are_never_compared_across_positions():
    """Ranking the pool on raw season points puts backup quarterbacks on top,
    because quarterbacks score more than receivers. A 284 point quarterback is
    not an upgrade on a 108 point receiver."""
    lineup = [player("My WR", "WR")]
    client = Client([PoolPlayer("Backup QB", "QB", 284.0)])
    assert find(client, {"My WR": 108.0}, lineup, set()) == []


def test_a_player_better_than_the_wire_is_not_flagged():
    lineup = [player("Good WR", "WR")]
    client = Client([PoolPlayer("Worse WR", "WR", 90.0)])
    assert find(client, {"Good WR": 200.0}, lineup, set()) == []


def test_a_stashed_player_is_left_alone():
    """He is on IR because you decided to keep him."""
    lineup = [player("Stashed", "WR", slot="IR", status="O")]
    client = Client([PoolPlayer("Better", "WR", 300.0)])
    assert find(client, {"Stashed": 100.0}, lineup, set()) == []


def test_a_player_already_claimed_is_not_offered_as_the_upgrade():
    """A pending claim means he is not available to add."""
    lineup = [player("Mine", "WR")]
    pool = [PoolPlayer("Claimed", "WR", 200.0), PoolPlayer("Free", "WR", 150.0)]
    client = Client(pool)
    client.league.transactions = lambda types=None: [
        type("Tx", (), {
            "status": "PENDING", "team": type("T", (), {"team_id": 6})(),
            "items": [type("I", (), {"type": "ADD", "playerId": "Claimed",
                                     "player": "Claimed"})()],
        })()]
    gaps = find(client, {"Mine": 100.0}, lineup, set())
    assert gaps[0].best_name == "Free"


def test_kickers_are_excluded_here_too():
    """Same reason as the waiver view: ESPN cannot tell kickers apart."""
    lineup = [player("My K", "K")]
    client = Client([PoolPlayer("Other K", "K", 200.0)])
    assert find(client, {"My K": 100.0}, lineup, set()) == []


def test_a_starter_is_flagged_but_marked_as_one():
    """Worth knowing, and worth knowing it is not a free swap."""
    lineup = [player("Starter", "WR", slot="WR")]
    client = Client([PoolPlayer("Better", "WR", 200.0)])
    gap = find(client, {"Starter": 100.0}, lineup, {"Starter"})[0]
    assert gap.in_lineup is True
    assert "currently starting" in gap.describe()


def test_the_only_player_at_a_position_is_marked():
    """Dropping your only quarterback is a different decision from dropping a
    fourth receiver, and the tool does not know which you want."""
    lineup = [player("Only QB", "QB"), player("WR A", "WR"), player("WR B", "WR")]
    client = Client([PoolPlayer("Better QB", "QB", 300.0),
                     PoolPlayer("Better WR", "WR", 300.0)])
    gaps = {g.name: g for g in find(
        client, {"Only QB": 100.0, "WR A": 100.0, "WR B": 100.0}, lineup, set())}
    assert gaps["Only QB"].only_one is True
    assert gaps["WR A"].only_one is False


def test_the_biggest_gap_comes_first():
    lineup = [player("Small", "WR"), player("Big", "TE")]
    client = Client([PoolPlayer("W", "WR", 120.0), PoolPlayer("T", "TE", 300.0)])
    gaps = find(client, {"Small": 100.0, "Big": 100.0}, lineup, set())
    assert [g.name for g in gaps] == ["Big", "Small"]


def test_a_player_with_no_season_projection_is_skipped():
    """Zero means unknown here, not worthless, and treating it as worthless
    would flag every player the projection source has never seen."""
    lineup = [player("Unknown", "WR")]
    client = Client([PoolPlayer("Better", "WR", 200.0)])
    assert find(client, {}, lineup, set()) == []
