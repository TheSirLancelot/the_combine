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


def test_a_partner_inside_the_noise_band_is_offered_and_labelled():
    """The band is the width below which these numbers cannot tell a gain from
    a loss. Requiring him to clear +band before a deal is worth mentioning hid
    the best deal in RCL: Davis for Nabers at +36 to me and +8 to him."""
    client = complementary()
    deals = T.find(client, band=10.0, limit=99)
    assert deals
    assert all(d.their_season > -10.0 for d in deals)
    inside = [d for d in deals if 0 < d.their_season <= 10.0]
    for d in inside:
        assert d.stretch, "a partner inside the band must be flagged, not hidden"


def test_a_deal_that_clearly_costs_the_partner_is_still_refused():
    """Relaxed is not removed. Below the band the numbers do say he loses."""
    deals = T.find(complementary(), band=10.0, limit=99)
    assert all(d.their_season > -10.0 for d in deals)


def test_solid_and_stretch_are_the_two_sides_of_the_same_band():
    d = T.Deal(give=player("A", "RB"), get=player("B", "QB"), partner="Them",
               my_season=30.0, their_season=8.0, my_week=0.0, their_week=0.0,
               bar=17.0)
    assert d.stretch is True
    assert T.Deal(give=player("A", "RB"), get=player("B", "QB"),
                  partner="Them", my_season=30.0, their_season=56.0,
                  my_week=0.0, their_week=0.0, bar=17.0).stretch is False


def test_two_balanced_rosters_produce_nothing_and_that_is_the_answer():
    """Identical rosters: every swap is a wash for me, so nothing clears MY
    bar, which is the side that stayed strict."""
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


def deal(give, get, mine=50.0, theirs=40.0):
    return T.Deal(give=player(give, "RB"), get=player(get, "QB"),
                  partner="Them", my_season=mine, their_season=theirs,
                  my_week=0.0, their_week=0.0)


def test_the_table_does_not_fill_with_one_trade_spelled_eight_ways():
    """Before this, every row was the one rival worth raiding paired with each
    man I could send back, and the deal on another roster never appeared."""
    kept = T._distinct([deal(f"Mine {i}", "Their Stud", mine=50.0 - i)
                        for i in range(5)], 8)
    assert len(kept) == 1


def test_each_target_keeps_its_own_best_price():
    """The bug William caught. Keying the dedupe on BOTH sides spent Davis on
    the first row, so the second row had to find another giver for Daniels and
    showed +28/+47 while hiding the identical-to-me +28/+60."""
    kept = T._distinct([deal("Davis", "Lamar", mine=32.0, theirs=56.0),
                        deal("Davis", "Daniels", mine=28.0, theirs=60.0),
                        deal("Pitre", "Daniels", mine=28.0, theirs=47.0)], 8)
    assert [(d.give.name, d.get.name) for d in kept] == [
        ("Davis", "Lamar"), ("Davis", "Daniels")]


def test_a_tie_for_me_goes_to_the_partner_who_gains_more():
    """He is the one who has to say yes, and leaving it to dictionary order
    threw away thirteen points of his gain for nothing."""
    deals = [deal("Pitre", "Daniels", mine=28.0, theirs=47.0),
             deal("Davis", "Daniels", mine=28.0, theirs=60.0)]
    deals.sort(key=lambda d: (-d.my_season, -d.their_season))
    assert deals[0].give.name == "Davis"


def test_repeated_givers_are_called_alternatives_not_a_package():
    both = T.alternatives_note([deal("Davis", "Lamar"), deal("Davis", "Daniels")])
    assert "Davis appears in more than one row" in both
    assert "alternatives, not a" in both
    one = T.alternatives_note([deal("Davis", "Lamar"), deal("Pitre", "Daniels")])
    assert "appears in more than one row" not in one
    # Either way, two rows never add up: each is priced against today's roster.
    assert "not worth the sum of the two" in both
    assert "not worth the sum of the two" in one


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
    assert "My RB2" in full
    assert "zero sum" in full and "not the same as them saying yes" in full
    assert "ASK" in full and "worth asking and" in full


# --- grading an offer somebody sent ----------------------------------------


def with_ir(client, name, pos, sn):
    """Park a player on IR. Cutting him frees no active spot, so he must never
    be offered up as the cheapest man on the roster."""
    client.teams["Mine"].append(
        player(name, pos, slot="IR", proj=0.0))
    client._season[name] = sn
    client.league.teams[0].roster = [
        Rostered(p.player_id, client._season.get(p.player_id, 0.0))
        for p in client.teams["Mine"]]
    return client


class Roomy(Client):
    """A client that can answer how many active roster spots are open."""

    def __init__(self, teams, season, capacity=4):
        super().__init__(teams, season)
        counts = {"RB": 1, "WR": 1, "BE": capacity - 2, "IR": 1}
        self.league.settings = type("S", (), {"position_slot_counts": counts})()


def test_it_prices_an_offer_from_both_ends():
    client = complementary()
    v, err = T.grade(client, ["My RB2"], ["Their WR2"])
    assert not err
    assert v.partner == "Rival"
    assert v.my_season > 0 and v.their_season > 0
    assert v.mutual and v.good


def test_it_says_what_actually_changes_in_the_lineup():
    """The number on its own is not an explanation. This is the part that
    answers 'but he is a starter, where did his points go'."""
    client = complementary()
    v, _ = T.grade(client, ["My WR1"], ["Their WR1"])
    joining = [m.name for m in v.season_moves if m.joining]
    leaving = [m.name for m in v.season_moves if not m.joining]
    assert joining == ["Their WR1"] and leaving == ["My WR1"]


def test_a_man_who_was_never_starting_moves_nothing():
    """Giving up a bench player changes the lineup not at all, and saying so is
    better than an empty section."""
    client = complementary()
    v, _ = T.grade(client, ["My RB2"], ["Their RB1"])
    assert v.season_moves == []
    assert v.my_season <= 0


def test_an_offer_against_you_reads_as_against_you():
    client = complementary()
    v, _ = T.grade(client, ["My RB1"], ["Their RB1"])
    assert v.my_season < 0 and not v.good


def test_taking_on_more_than_you_send_forces_a_cut():
    client = Roomy({
        "Mine": [player("Star", "RB", slot="RB", proj=15.0),
                 player("Filler", "WR", slot="WR", proj=3.0),
                 player("Spare", "WR", proj=2.0),
                 player("Deadweight", "WR", proj=1.0)],
        "Rival": [player("Theirs A", "WR", slot="WR", proj=14.0),
                  player("Theirs B", "WR", proj=13.0)]},
        {"Star": 300.0, "Filler": 90.0, "Spare": 60.0, "Deadweight": 30.0,
         "Theirs A": 280.0, "Theirs B": 260.0}, capacity=4)
    v, err = T.grade(client, ["Star"], ["Theirs A", "Theirs B"])
    assert not err
    assert v.spots == -1              # one more in than out
    assert v.room == 0                # four men, four active spots
    assert v.my_cuts == ["Deadweight"]


def test_the_cut_is_chosen_on_the_roster_as_it_would_be_after_the_trade():
    """Ask before the deal lands and the man who is about to become surplus
    still looks like a starter, so the cut falls on somebody useful."""
    client = Roomy({
        "Mine": [player("Old RB", "RB", slot="RB", proj=10.0),
                 player("Keeper", "WR", slot="WR", proj=9.0),
                 player("Scrub", "WR", proj=1.0),
                 player("Chaff", "WR", proj=1.0)],
        "Rival": [player("New RB", "RB", slot="RB", proj=20.0),
                  player("Throw-in", "WR", proj=8.0)]},
        {"Old RB": 100.0, "Keeper": 190.0, "Scrub": 40.0, "Chaff": 20.0,
         "New RB": 400.0, "Throw-in": 170.0}, capacity=4)
    v, err = T.grade(client, ["Old RB"], ["New RB", "Throw-in"])
    assert not err
    assert v.my_cuts == ["Chaff"]     # the cheapest, not the newly surplus man


def test_an_ir_stash_is_never_the_cheapest_man_to_cut():
    client = Roomy({
        "Mine": [player("Star", "RB", slot="RB", proj=15.0),
                 player("Filler", "WR", slot="WR", proj=3.0),
                 player("Spare", "WR", proj=2.0)],
        "Rival": [player("Theirs A", "WR", slot="WR", proj=14.0),
                  player("Theirs B", "WR", proj=13.0)]},
        {"Star": 300.0, "Filler": 90.0, "Spare": 60.0,
         "Theirs A": 280.0, "Theirs B": 260.0}, capacity=3)
    with_ir(client, "Hurt Man", "WR", 10.0)
    v, _ = T.grade(client, ["Star"], ["Theirs A", "Theirs B"])
    assert "Hurt Man" not in v.my_cuts
    assert v.my_cuts == ["Spare"]


def test_sending_more_than_you_take_frees_a_spot():
    client = complementary()
    v, _ = T.grade(client, ["My RB1", "My RB2"], ["Their WR1"])
    assert v.spots == 1
    assert v.my_cuts == []


def test_a_name_on_nobody_s_roster_is_refused():
    v, err = T.grade(complementary(), ["My RB2"], ["Nobody At All"])
    assert v is None and "not on any other roster" in err


def test_one_of_your_own_cannot_be_on_the_receiving_side():
    v, err = T.grade(complementary(), ["My RB2"], ["My RB1"])
    assert v is None and "not on any other roster" in err


def test_a_three_way_trade_is_refused_rather_than_guessed_at():
    mine = [player("Mine", "RB", slot="RB", proj=10.0)]
    client = Client({"Mine": mine,
                     "A": [player("From A", "WR", slot="WR", proj=10.0)],
                     "B": [player("From B", "WR", slot="WR", proj=10.0)]},
                    {"Mine": 200.0, "From A": 200.0, "From B": 200.0})
    v, err = T.grade(client, ["Mine"], ["From A", "From B"])
    assert v is None and "three way" in err


def test_an_empty_side_is_not_a_trade():
    v, err = T.grade(complementary(), [], ["Their WR1"])
    assert v is None and "at least one player each way" in err


def test_the_verdict_render_leads_with_the_answer():
    v, _ = T.grade(complementary(), ["My RB2"], ["Their WR1"])
    text = T.render_verdict(v, "A League")
    assert "THE NUMBERS FAVOUR YOU" in text
    assert "WHAT CHANGES" in text
    assert "does not tell you to accept it" in text
