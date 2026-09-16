"""Deals where both rosters gain.

The properties worth pinning are the ones that were got wrong on the way here.
A trade has to be priced on the SEASON assignment, because the weekly one is
close to zero sum and finds nothing. The season assignment must not inherit
this week's kickoff locks. A man outside the optimal lineup costs nothing to
lose, and that shortcut has to agree with solving it the long way. And the
table must not fill up with eight spellings of one trade.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline import trades as T
from combine.platforms import Matchup, ProGame, WeeklyPlayer

FUTURE = 4_000_000_000_000
PAST = 1_000_000_000_000


def player(name, pos, slot="BE", proj=10.0, kickoff=FUTURE, eligible=None):
    return WeeklyPlayer(
        player_id=name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset(eligible or {pos, "BE"}), projected=proj,
        game=ProGame(opponent="SF", home=True, kickoff_ms=kickoff))


class Rostered:
    """What `league.teams[*].roster` hands back: season projections live here
    and nowhere else."""

    def __init__(self, pid, season):
        self.playerId = pid
        self.projected_total_points = season


class Client:
    week = 2

    def __init__(self, teams: dict[str, list], season: dict[str, float]):
        self.teams = teams
        self._season = season
        self.league = type("L", (), {"teams": [
            type("T", (), {"roster": [Rostered(p.player_id,
                                               season.get(p.player_id, 0.0))
                                      for p in players]})()
            for players in teams.values()]})()

    def roster_slots(self):
        return {"RB": 1, "WR": 1}

    def matchup(self, week=None):
        mine = self.teams["Mine"]
        return Matchup(week=2, home_team="Mine", away_team="Rival",
                       home_proj=0.0, away_proj=0.0, home_lineup=mine,
                       away_lineup=self.teams.get("Rival", []), mine="home")

    def player_weeks(self, week):
        return [(team, "x", p) for team, players in self.teams.items()
                for p in players]


def complementary():
    """I am deep at back and empty at receiver. They are the mirror. This is the
    only shape that makes a one-for-one work for both sides, and the whole point
    of the module is finding it."""
    mine = [player("My RB1", "RB", slot="RB", proj=15.0),
            player("My RB2", "RB", proj=12.0),
            player("My WR1", "WR", slot="WR", proj=4.0)]
    theirs = [player("Their WR1", "WR", slot="WR", proj=15.0),
              player("Their WR2", "WR", proj=12.0),
              player("Their RB1", "RB", slot="RB", proj=4.0)]
    season = {"My RB1": 300.0, "My RB2": 240.0, "My WR1": 80.0,
              "Their WR1": 300.0, "Their WR2": 240.0, "Their RB1": 80.0}
    return Client({"Mine": mine, "Rival": theirs}, season)


def test_it_finds_the_deal_that_helps_both_rosters():
    deals = T.find(complementary(), band=10.0)
    assert deals, "a surplus back for a surplus receiver is the whole point"
    best = deals[0]
    assert best.give.name == "My RB2"
    # Their starter, not their spare. Both versions work for both sides, and
    # the ranking is by MY gain, which is the one to ask for first.
    assert best.get.name == "Their WR1"
    assert best.my_season > 0 and best.their_season > 0


def test_the_surplus_man_is_the_one_offered_not_the_starter():
    """Giving up RB1 also 'helps' if the return is big enough. It should not
    win, because RB2 costs nothing to lose and RB1 costs a starting slot."""
    deals = T.find(complementary(), band=10.0, limit=99)
    gave = [d.give.name for d in deals]
    assert gave[0] == "My RB2"


def test_two_balanced_rosters_produce_nothing_and_that_is_the_answer():
    same = {"A1": 300.0, "A2": 200.0, "B1": 300.0, "B2": 200.0}
    client = Client({
        "Mine": [player("A1", "RB", slot="RB", proj=15.0),
                 player("A2", "WR", slot="WR", proj=12.0)],
        "Rival": [player("B1", "RB", slot="RB", proj=15.0),
                  player("B2", "WR", slot="WR", proj=12.0)]}, same)
    assert T.find(client, band=10.0) == []


def test_the_season_assignment_ignores_this_week_s_kickoffs():
    """Read on a Tuesday every game has been played, so `as_candidate` marks
    the whole roster unplayable. If that leaked into the season axis the bench
    would vanish and no roster would appear to have any surplus at all."""
    client = complementary()
    for team, players in client.teams.items():
        client.teams[team] = [
            player(p.name, p.pos, slot=p.slot, proj=p.projected, kickoff=PAST)
            for p in players]
    assert T.find(client, band=10.0), "locked games must not empty the roster"


def test_a_man_outside_the_lineup_costs_nothing_to_lose():
    """The shortcut that keeps this in seconds. It has to agree with solving
    every drop the long way, or the search is quietly ranking on a lie."""
    client = complementary()
    cal = None
    season = T.season_projections(client)
    cands = [T._cand(p, cal, season) for p in client.teams["Mine"]]
    slot_list = ["RB", "WR"]
    chosen = T._assignment(cands, slot_list)
    base = sum(T._season(c) for c in chosen)
    quick = T._drop_costs(cands, slot_list, base, T._season,
                          {c["espn_id"] for c in chosen})
    slow = {c["espn_id"]: base - T._value(cands[:i] + cands[i + 1:], slot_list,
                                          T._season)
            for i, c in enumerate(cands)}
    assert quick == slow
    assert quick["My RB2"] == 0.0


def test_the_table_does_not_fill_with_one_trade_spelled_eight_ways():
    """Before this, every row was the one rival worth raiding paired with each
    man I could send back, and the deal on another roster never appeared."""
    deals = [T.Deal(give=player(f"Mine {i}", "RB"), get=player("Their Stud", "QB"),
                    partner="Them", my_season=50.0 - i, their_season=40.0,
                    my_week=0.0, their_week=0.0)
             for i in range(5)]
    kept = T._distinct(deals, 8)
    assert len(kept) == 1


def test_a_player_espn_has_no_season_number_for_is_left_out():
    """A missing projection is zero, and a zero reads as 'worth nothing to
    anybody', which would offer him up for free."""
    client = complementary()
    client._season.pop("My RB2")
    client.league.teams[0].roster = [Rostered(p.player_id,
                                              client._season.get(p.player_id, 0.0))
                                     for p in client.teams["Mine"]]
    assert all(d.give.name != "My RB2" for d in T.find(client, band=10.0))


def test_render_says_what_it_found_or_says_why_it_found_nothing():
    empty = T.render([], "A League")
    assert "noise band" in empty and "surpluses fit" in empty
    full = T.render(T.find(complementary(), band=10.0), "A League")
    assert "My RB2" in full and "Their WR2" in full
    assert "zero sum" in full and "not the same as them saying yes" in full
