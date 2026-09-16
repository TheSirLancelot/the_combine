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
from itertools import pairwise
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


class Pooled:
    """What `league.free_agents` hands back."""

    def __init__(self, name, pos, season, proj=0.0):
        self.name = name
        self.position = pos
        self.proTeam = "KC"
        self.playerId = name
        self.injuryStatus = "ACTIVE"
        self.eligibleSlots = [pos, "BE"]
        self.projected_total_points = season
        self.stats = {w: {"projected_points": proj} for w in range(1, 19)}


class Client:
    week = 2

    def __init__(self, teams: dict[str, list], season: dict[str, float],
                 wire=()):
        self.teams = teams
        self._season = season
        self.league = type("L", (), {"teams": [
            type("T", (), {"roster": [Rostered(p.player_id,
                                               season.get(p.player_id, 0.0))
                                      for p in players]})()
            for players in teams.values()]})()
        self.league.free_agents = lambda size=350: list(wire)

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


def test_the_partner_s_side_of_the_cascade_is_reported():
    """William's question: how does losing their starting quarterback gain them
    56 points. Because the man behind him is worth nearly as much, so the loss
    is the difference and not the projection. A number that needs a probe to
    explain belongs in the output."""
    mine = [player("My LB", "LB", slot="LB", proj=9.0),
            player("My QB", "QB", slot="QB", proj=18.0)]
    theirs = [player("Their QB1", "QB", slot="QB", proj=21.0),
              player("Their QB2", "QB", proj=20.0),
              player("Their LB", "LB", slot="LB", proj=4.0)]
    season = {"My LB": 200.0, "My QB": 280.0,
              "Their QB1": 343.0, "Their QB2": 339.0, "Their LB": 117.0}
    client = Client({"Mine": mine, "Rival": theirs}, season)
    client.roster_slots = lambda: {"QB": 1, "LB": 1}

    v, err = T.grade(client, ["My LB"], ["Their QB1"])
    assert not err
    joining = {m.name: m.value for m in v.their_moves if m.joining}
    leaving = {m.name: m.value for m in v.their_moves if not m.joining}
    assert joining == {"Their QB2": 339.0, "My LB": 200.0}
    assert leaving == {"Their QB1": 343.0, "Their LB": 117.0}
    # Four points of quarterback lost, eighty three of linebacker gained.
    assert v.their_season == 79.0


def test_why_they_might_reads_as_a_sentence_or_says_nothing():
    d = deal("A", "B")
    assert d.why_they_might() == ""        # nothing computed for this one
    with_moves = T.Deal(
        give=player("A", "RB"), get=player("B", "QB"), partner="Them",
        my_season=30.0, their_season=56.0, my_week=0.0, their_week=0.0,
        their_moves=(T.Move("Backup QB", "QB", 339.0, True),
                     T.Move("Star QB", "QB", 343.0, False)))
    text = with_moves.why_they_might()
    assert "they start Backup QB 339" in text
    assert "out comes Star QB 343" in text


# --- reading the shape of somebody else's roster ----------------------------


def profiles(**teams):
    """{team: ({pos: worst starter}, {pos: best benched})}."""
    return {name: (rows[0], rows[1]) for name, rows in teams.items()}


def test_thin_means_worse_than_the_league_not_worse_than_your_own_quarterback():
    """Every roster's lowest-scoring starter is a receiver, because receivers
    fill the most slots and score less than quarterbacks. Comparing a roster
    with itself therefore says 'thin at WR' about everybody, which is what
    produced 'thinnest at WR, carrying spare WRs' on screen."""
    shape = T.league_shape(profiles(
        A=({"QB": 300.0, "WR": 100.0}, {}),
        B=({"QB": 300.0, "WR": 190.0}, {}),
        C=({"QB": 300.0, "WR": 200.0}, {})), band=17.0)
    assert shape["A"][0] == "WR"          # genuinely below the league at WR
    assert shape["B"][0] == ""            # ordinary, so nothing is named
    assert shape["C"][0] == ""


def test_a_position_within_the_noise_band_is_not_called_a_hole():
    shape = T.league_shape(profiles(
        A=({"RB": 195.0}, {}), B=({"RB": 200.0}, {}),
        C=({"RB": 205.0}, {})), band=17.0)
    assert all(thin == "" for thin, _deep in shape.values())


def test_deep_means_a_better_bench_than_the_league_has():
    """The two-quarterback roster: Daniels at 339 on the bench is not depth in
    the sense of 'lots of quarterbacks', it is a startable one nobody else is
    sitting on."""
    shape = T.league_shape(profiles(
        A=({}, {"QB": 339.0, "WR": 120.0}),
        B=({}, {"QB": 90.0, "WR": 125.0}),
        C=({}, {"QB": 95.0, "WR": 130.0})), band=17.0)
    assert shape["A"][1] == "QB"
    assert shape["B"][1] == "" and shape["C"][1] == ""


def test_a_roster_is_never_called_thin_and_deep_at_the_same_position():
    """The contradiction itself. It cannot recur by construction now: a benched
    man good enough to beat the league's starters would be starting."""
    mine = [player("A RB", "RB", slot="RB", proj=15.0),
            player("A WR", "WR", slot="WR", proj=4.0),
            player("Bench WR", "WR", proj=3.0)]
    theirs = [player("B RB", "RB", slot="RB", proj=14.0),
              player("B WR", "WR", slot="WR", proj=13.0),
              player("B Bench", "WR", proj=12.0)]
    season = {"A RB": 300.0, "A WR": 80.0, "Bench WR": 70.0,
              "B RB": 290.0, "B WR": 260.0, "B Bench": 240.0}
    client = Client({"Mine": mine, "Rival": theirs}, season)
    for d in T.find(client, band=10.0, limit=99):
        assert not (d.partner_thin and d.partner_thin == d.partner_deep)


def test_the_profile_splits_starters_from_the_bench_by_the_assignment():
    """Not by this week's slot. A man ESPN has on the bench may be the best
    player a roster owns over a season, and vice versa."""
    cands = [T._cand(p, None, {"Starter": 300.0, "Backup": 250.0})
             for p in (player("Starter", "RB", slot="BE", proj=1.0),
                       player("Backup", "RB", slot="RB", proj=20.0))]
    chosen = T._assignment(cands, ["RB"])
    worst, best = T._profile(cands, ["RB"], chosen)
    assert worst == {"RB": 300.0}      # the assignment starts the better man
    assert best == {"RB": 250.0}


# --- going after one man in particular --------------------------------------


def targetable():
    """The shape that makes a one-for-one work at all: they hold two men at a
    position that only starts one, so the star is nearly free for them to move.

    My side is deep at back and has exactly one receiver, which matters for the
    pruning test: losing him empties a starting slot.
    """
    mine = [player("My QB", "QB", slot="QB", proj=15.0),
            player("My RB1", "RB", slot="RB", proj=14.0),
            player("My RB2", "RB", proj=13.0),
            player("My RB3", "RB", proj=12.0),
            player("My WR", "WR", slot="WR", proj=11.0)]
    theirs = [player("Their QB1", "QB", slot="QB", proj=20.0),
              player("Their QB2", "QB", proj=19.0),
              player("Their RB", "RB", slot="RB", proj=5.0),
              player("Their WR", "WR", slot="WR", proj=8.0)]
    season = {"My QB": 250.0, "My RB1": 300.0, "My RB2": 280.0,
              "My RB3": 270.0, "My WR": 200.0,
              "Their QB1": 340.0, "Their QB2": 330.0,
              "Their RB": 100.0, "Their WR": 150.0}
    client = Client({"Mine": mine, "Rival": theirs}, season)
    client.roster_slots = lambda: {"QB": 1, "RB": 1, "WR": 1}
    return client


def test_it_finds_a_way_to_land_the_man_you_named():
    items, err = T.packages(targetable(), "Their QB1", band=10.0)
    assert not err and items
    assert all(p.get.name == "Their QB1" for p in items)
    assert all(p.partner == "Rival" for p in items)


def test_the_top_rung_is_the_cheapest_ask_not_the_most_generous():
    """A spare back costs me nothing on this axis, so adding him to the ask
    scores the same and reads as better because it gives the other man more. It
    is not better: handing over a player for nothing costs depth that none of
    these numbers price, so the smaller package is the one to ask for first."""
    items, _ = T.packages(targetable(), "Their QB1", band=10.0)
    assert len(items[0].give) == 1
    assert items[0].my_season == max(p.my_season for p in items)


def test_each_rung_costs_more_and_is_worth_more_to_the_other_side():
    items, _ = T.packages(targetable(), "Their QB1", band=10.0)
    assert len(items) > 1, "this roster has a real ladder to show"
    for above, below in pairwise(items):
        assert below.their_season > above.their_season + 10.0
        assert below.my_season <= above.my_season


def test_a_man_who_fails_on_his_own_never_appears_in_any_package():
    """The bound that makes this a lattice search rather than a sample. For any
    set S, `M - S + X` is inside `M - p + X` for every p in S, and the
    assignment is monotone, so a package can never beat what its worst member
    scores alone. My only receiver empties a starting slot, so he fails on his
    own and no package he is in is ever solved."""
    client = targetable()
    alone, _ = T.packages(client, "Their QB1", band=10.0, max_out=1, limit=99)
    assert all("My WR" not in p.names for p in alone)
    everything, _ = T.packages(client, "Their QB1", band=10.0, max_out=3,
                               limit=99)
    assert all("My WR" not in p.names for p in everything)


def solves(client, **kw):
    """How many assignments a search actually costs."""
    counted = []
    real = T._value

    def spy(cands, slot_list, key):
        counted.append(1)
        return real(cands, slot_list, key)

    T._value = spy
    try:
        T.packages(client, "Their QB1", band=10.0, **kw)
    finally:
        T._value = real
    return len(counted)


def test_the_bound_actually_cuts_the_search():
    """Five players is 5 singles, 10 pairs and 10 triples. Going from singles
    to triples must not cost the 20 extra sets in full, or the bound is
    decoration."""
    client = targetable()
    singles_only = solves(client, max_out=1, limit=99)
    everything = solves(client, max_out=3, limit=99)
    assert everything - singles_only < 20 * 2


def test_a_player_nobody_owns_is_refused():
    items, err = T.packages(targetable(), "Nobody At All")
    assert items == [] and "not on any other roster" in err


def test_asking_for_one_of_your_own_is_refused():
    items, err = T.packages(targetable(), "My RB1")
    assert items == [] and "not on any other roster" in err


def test_a_man_who_would_not_start_for_you_is_still_priced():
    """The buy-low, and the reason this stopped demanding a gain. He is worse
    than what I already start, so he gains me nothing by definition, and
    refusing to price him answered a question nobody asked. What he costs is
    the answer."""
    items, err = T.packages(targetable(), "Their WR", band=10.0)
    assert not err and items, "a man you named should come back with a price"
    assert all(p.bench for p in items)
    assert items[0].my_season <= 0


def test_the_break_even_is_what_he_has_to_beat_his_projection_by():
    """The only honest way to price a buy-low. ESPN's number is the only view
    of him this system has, so it cannot say he is undervalued. It can say by
    how much he would have to be."""
    items, _ = T.packages(targetable(), "Their WR", band=10.0)
    assert items[0].breakeven == -items[0].my_season
    gaining = T.packages(targetable(), "Their QB1", band=10.0)[0]
    assert gaining[0].my_season > 0
    assert gaining[0].breakeven == 0.0, "no bet to price when it already gains"


def test_a_man_who_would_start_is_not_flagged_as_bench():
    items, _ = T.packages(targetable(), "Their QB1", band=10.0)
    assert not items[0].bench


def test_cover_says_what_he_is_worth_if_the_man_ahead_of_him_is_out():
    """The handcuff question. It does not say how likely an injury is, because
    nothing here knows that. It says what having him is worth if it happens."""
    client = targetable()
    season = T.season_projections(client)
    slot_list = ["QB", "RB", "WR"]
    cands = [T._cand(p, None, season) for p in client.teams["Mine"]]
    # A second receiver behind my only one: worthless while the starter plays,
    # worth the whole slot the moment he does not.
    spare = player("Spare WR", "WR", proj=5.0)
    season["Spare WR"] = 190.0
    cands.append(T._cand(spare, None, season, incoming=True))
    name, saved = T.cover_value(cands, slot_list, "Spare WR")
    assert name == "My WR"
    assert saved == 190.0


def test_cover_is_nothing_when_the_men_ahead_do_not_share_a_slot():
    client = targetable()
    season = T.season_projections(client)
    cands = [T._cand(p, None, season) for p in client.teams["Mine"]]
    name, saved = T.cover_value(cands, ["QB", "RB", "WR"], "nobody")
    assert name == "" and saved == 0.0


def test_render_says_why_it_found_nothing():
    text = T.render_packages([], "Nobody", "A League")
    assert "not an upgrade" in text and "price is more than" in text


def test_render_leads_with_the_cheapest_ask():
    items, _ = T.packages(targetable(), "Their QB1", band=10.0)
    text = T.render_packages(items, "Their QB1", "A League")
    assert "going after Their QB1" in text
    assert "Cheapest ask first" in text
    assert items[0].names in text


def test_the_search_reports_progress_as_it_goes():
    """Fifty seconds of silence is indistinguishable from a hang, so the caller
    gets told which partner is being priced."""
    seen = []
    T.find(complementary(), band=10.0,
           progress=lambda done, total, team: seen.append((done, total, team)))
    assert seen[0][2] is None, "one call before anything is read"
    assert seen[-1][2] == "", "and one when it is finished"
    partners = [team for _d, _t, team in seen if team]
    assert partners == ["Rival"]
    assert seen[-1][0] == seen[-1][1], "the last call is a full bar"


def test_progress_is_optional():
    assert T.find(complementary(), band=10.0) == T.find(
        complementary(), band=10.0, progress=None)


# --- going through one manager's roster -------------------------------------


def three_teams():
    """Two rival rosters, and nothing on mine that a rival wants badly.

    My only spare is a scrub, so the good player on each of their rosters can
    only be had by asking for something they have a reason to refuse. That is
    the shape the reach setting exists for.
    """
    mine = [player("My RB1", "RB", slot="RB", proj=15.0),
            player("My WR1", "WR", slot="WR", proj=4.0),
            player("My Scrub", "WR", proj=1.0)]
    willing = [player("Willing WR1", "WR", slot="WR", proj=15.0),
               player("Willing WR2", "WR", proj=13.0),
               player("Willing RB", "RB", slot="RB", proj=4.0)]
    quiet = [player("Quiet WR1", "WR", slot="WR", proj=14.0),
             player("Quiet WR2", "WR", proj=12.0),
             player("Quiet RB", "RB", slot="RB", proj=5.0)]
    season = {"My RB1": 300.0, "My WR1": 80.0, "My Scrub": 20.0,
              "Willing WR1": 300.0, "Willing WR2": 250.0, "Willing RB": 80.0,
              "Quiet WR1": 290.0, "Quiet WR2": 240.0, "Quiet RB": 90.0}
    return Client({"Mine": mine, "Willing": willing, "Quiet": quiet}, season)


def test_narrowing_to_one_team_leaves_the_others_out():
    deals = T.find(three_teams(), band=10.0, limit=99, only="Willing")
    assert deals
    assert {d.partner for d in deals} == {"Willing"}


def test_the_whole_league_still_sees_both_of_them():
    deals = T.find(three_teams(), band=10.0, limit=99)
    assert {d.partner for d in deals} == {"Willing", "Quiet"}


def test_a_partial_team_name_is_enough():
    assert T.find(three_teams(), band=10.0, only="willi")


def test_a_team_nobody_is_called_returns_nothing_rather_than_everything():
    """Falling back to the whole league on a typo would quietly answer a
    different question."""
    assert T.find(three_teams(), band=10.0, only="Nobody FC") == []


def test_reach_is_how_far_below_zero_his_side_may_land():
    """Their best receiver can only be had by asking for something the numbers
    say hurts them. At reach 1 that deal is not listed; pushing surfaces it."""
    strict = T.find(three_teams(), band=10.0, limit=99, only="Willing")
    pushy = T.find(three_teams(), band=10.0, limit=99, only="Willing",
                   reach=6.0)
    assert all(d.their_season > -10.0 for d in strict)
    assert all(d.their_season > -60.0 for d in pushy)
    assert len(pushy) > len(strict), "pushing has to surface something new"
    assert "Willing WR1" not in {d.get.name for d in strict}
    assert "Willing WR1" in {d.get.name for d in pushy}
    assert any(d.ask == "longshot" for d in pushy)


def test_ask_has_three_levels_and_they_are_bands_of_noise():
    def deal_at(theirs):
        return T.Deal(give=player("A", "RB"), get=player("B", "QB"),
                      partner="Them", my_season=30.0, their_season=theirs,
                      my_week=0.0, their_week=0.0, bar=17.0)

    assert deal_at(56.0).ask == "solid"
    assert deal_at(8.0).ask == "stretch"
    assert deal_at(-8.0).ask == "stretch"
    assert deal_at(-29.0).ask == "longshot"


def test_the_render_names_the_team_when_there_is_one():
    deals = T.find(three_teams(), band=10.0, only="Willing")
    text = T.render(deals, "A League", focus="Willing")
    assert "trades with Willing" in text
    empty = T.render([], "A League", focus="Willing")
    assert "Nothing with Willing" in empty and "asking for more" in empty


# --- sending somebody you have not claimed yet ------------------------------
#
# The scenario: a quarterback clears waivers, and you want him as the currency
# in a trade rather than as a starter. It is two moves, and the first one costs
# a roster spot before the second gives it back.


def with_wire(capacity=4, wire=()):
    mine = [player("My RB", "RB", slot="RB", proj=15.0),
            player("My WR", "WR", slot="WR", proj=9.0),
            player("Spare", "WR", proj=1.0)]
    theirs = [player("Their WR", "WR", slot="WR", proj=16.0),
              player("Their RB", "RB", slot="RB", proj=3.0)]
    season = {"My RB": 300.0, "My WR": 190.0, "Spare": 40.0,
              "Their WR": 320.0, "Their RB": 60.0}
    client = Roomy({"Mine": mine, "Rival": theirs}, season, capacity=capacity)
    client.league.free_agents = lambda size=350: list(wire)
    return client


def test_a_free_agent_can_be_the_thing_you_send():
    """He is currency, not a starter. Refusing to price him answered a
    different question from the one being asked."""
    client = with_wire(capacity=4, wire=[Pooled("Free QB", "QB", 280.0)])
    v, err = T.grade(client, ["Free QB"], ["Their WR"])
    assert not err, err
    assert v.claimed == ["Free QB"]
    assert [p.name for p in v.give] == ["Free QB"]


def test_with_a_spot_open_the_claim_costs_nothing():
    client = with_wire(capacity=5, wire=[Pooled("Free QB", "QB", 280.0)])
    v, _ = T.grade(client, ["Free QB"], ["Their WR"])
    assert v.claim_cuts == []


def test_with_no_spot_open_the_claim_costs_a_player_and_it_is_priced():
    """Three men and three active spots, so landing him means dropping
    somebody, and that drop is a real loss rather than a footnote."""
    client = with_wire(capacity=3, wire=[Pooled("Free QB", "QB", 280.0)])
    v, err = T.grade(client, ["Free QB"], ["Their WR"])
    assert not err, err
    assert v.claim_cuts == ["Spare"], v.claim_cuts


def test_a_worthless_drop_makes_him_genuinely_free_currency():
    """Claiming him costs exactly whoever you drop to fit him in. When that is
    a man contributing nothing, the claim really is free, and sending him is
    identical to sending the man you dropped."""
    wire = [Pooled("Free QB", "QB", 280.0)]
    claimed, _ = T.grade(with_wire(capacity=3, wire=wire),
                         ["Free QB"], ["Their WR"])
    owned, _ = T.grade(with_wire(capacity=3), ["Spare"], ["Their WR"])
    assert claimed.claim_cuts == ["Spare"]
    assert claimed.my_season == owned.my_season


def test_a_claim_that_forces_a_real_drop_is_priced_as_the_loss_it_is():
    """The trap this exists to catch. Landing a quarterback with no room drops
    your own, and then you trade the new one away, and the slot is empty. The
    numbers have to show that rather than calling him free."""
    def roster(capacity):
        mine = [player("My RB", "RB", slot="RB", proj=15.0),
                player("My WR", "WR", slot="WR", proj=9.0),
                player("My QB", "QB", slot="QB", proj=14.0)]
        theirs = [player("Their WR", "WR", slot="WR", proj=16.0),
                  player("Their RB", "RB", slot="RB", proj=3.0)]
        season = {"My RB": 300.0, "My WR": 190.0, "My QB": 200.0,
                  "Their WR": 320.0, "Their RB": 60.0}
        client = Roomy({"Mine": mine, "Rival": theirs}, season,
                       capacity=capacity)
        client.roster_slots = lambda: {"QB": 1, "RB": 1, "WR": 1}
        client.league.free_agents = lambda size=350: [
            Pooled("Free QB", "QB", 280.0)]
        return client

    tight, _ = T.grade(roster(3), ["Free QB"], ["Their WR"])
    roomy, _ = T.grade(roster(4), ["Free QB"], ["Their WR"])
    assert tight.claim_cuts == ["My QB"]
    assert roomy.claim_cuts == []
    assert tight.my_season < roomy.my_season, (
        "dropping your own quarterback to rent one for a trade is a cost")
    assert tight.my_season < 0


def test_somebody_else_s_player_on_the_give_side_says_whose_he_is():
    client = with_wire(wire=[Pooled("Free QB", "QB", 280.0)])
    v, err = T.grade(client, ["Their RB"], ["Their WR"])
    assert v is None
    assert "is on Rival's roster" in err and "not yours to send" in err


def test_a_name_on_nobody_s_roster_and_not_on_the_wire_is_refused():
    client = with_wire(wire=[Pooled("Free QB", "QB", 280.0)])
    v, err = T.grade(client, ["Nobody At All"], ["Their WR"])
    assert v is None and "your roster or the wire" in err


def test_the_pool_does_not_refuse_men_the_waiver_view_would():
    """`waivers._synthetic` drops a kicker, a man ruled out this week and
    anyone with no projection today. All correct for Sunday, all wrong for a
    player being valued as currency."""
    client = with_wire(wire=[Pooled("Free K", "K", 150.0, proj=0.0)])
    season = {}
    found = T.pool(client, 2, season)
    assert "free k" in found
    assert season[found["free k"].player_id] == 150.0


def test_the_render_says_it_is_two_moves():
    client = with_wire(capacity=3, wire=[Pooled("Free QB", "QB", 280.0)])
    v, _ = T.grade(client, ["Free QB"], ["Their WR"])
    text = " ".join(T.render_verdict(v, "A League").split())
    assert "not on your roster yet" in text
    assert "two moves and the first one can fail" in text
    assert "Spare" in text


# --- a claim you have already put in ----------------------------------------


class Claimed(Roomy):
    """A roster with a pending waiver claim of my own already in."""

    def __init__(self, *a, claims=(), wire=(), **kw):
        super().__init__(*a, **kw)
        self.league.free_agents = lambda size=350: list(wire)
        self._claims = list(claims)
        self.cfg = type("C", (), {"team_id": 6})()
        self.league.transactions = lambda types=None: self._claims


def claim_row(player_id, name, drop=""):
    """One PENDING transaction shaped the way espn-api hands them back."""
    items = [type("I", (), {"type": "ADD", "playerId": player_id,
                            "player": name})()]
    if drop:
        items.append(type("I", (), {"type": "DROP", "playerId": drop,
                                    "player": drop})())
    return type("T", (), {
        "team": type("T", (), {"team_id": 6})(), "date": 1_700_000_000,
        "status": "PENDING", "items": items})()


def claimable(capacity=3, claims=()):
    mine = [player("My RB", "RB", slot="RB", proj=15.0),
            player("My WR", "WR", slot="WR", proj=9.0),
            player("Spare", "WR", proj=1.0)]
    theirs = [player("Their WR", "WR", slot="WR", proj=16.0),
              player("Their RB", "RB", slot="RB", proj=3.0)]
    season = {"My RB": 300.0, "My WR": 190.0, "Spare": 40.0,
              "Their WR": 320.0, "Their RB": 60.0}
    return Claimed({"Mine": mine, "Rival": theirs}, season,
                   capacity=capacity, claims=claims,
                   wire=[Pooled("Free QB", "QB", 280.0)])


def test_a_claim_you_already_made_decides_the_drop():
    """Guessing a different one answers a question you already answered, and
    the guess is cheapest-first, which is rarely what somebody picks."""
    client = claimable(claims=[claim_row("Free QB", "Free QB", drop="My WR")])
    v, err = T.grade(client, ["Free QB"], ["Their WR"])
    assert not err, err
    assert v.claim_known == (("Free QB", "My WR"),)
    assert v.claim_cuts == ["My WR"], "not the cheapest man, the one you chose"


def test_without_a_claim_it_guesses_and_says_it_is_guessing():
    client = claimable()
    v, _ = T.grade(client, ["Free QB"], ["Their WR"])
    assert v.claim_known == ()
    assert v.claim_cuts == ["Spare"]
    text = " ".join(T.render_verdict(v, "A League").split())
    assert "cheapest first" in text
    assert "put the claim in first and ask again" in text


def test_the_named_drop_is_actually_removed_from_the_valuation():
    """Saying whose spot it is and then pricing somebody else's would be worse
    than guessing. Here the choice really matters: dropping the quarterback to
    rent one, then trading the rented one away, leaves the slot empty."""
    def client(claims=()):
        mine = [player("My QB", "QB", slot="QB", proj=14.0),
                player("My RB", "RB", slot="RB", proj=15.0),
                player("My WR", "WR", slot="WR", proj=9.0),
                player("Spare", "WR", proj=1.0)]
        theirs = [player("Their WR", "WR", slot="WR", proj=16.0),
                  player("Their RB", "RB", slot="RB", proj=3.0)]
        season = {"My QB": 200.0, "My RB": 300.0, "My WR": 190.0,
                  "Spare": 40.0, "Their WR": 320.0, "Their RB": 60.0}
        out = Claimed({"Mine": mine, "Rival": theirs}, season, capacity=4,
                      claims=claims, wire=[Pooled("Free QB", "QB", 280.0)])
        out.roster_slots = lambda: {"QB": 1, "RB": 1, "WR": 1}
        return out

    guessed, _ = T.grade(client(), ["Free QB"], ["Their WR"])
    named, _ = T.grade(client([claim_row("Free QB", "Free QB", drop="My QB")]),
                       ["Free QB"], ["Their WR"])
    assert guessed.claim_cuts == ["Spare"] and guessed.my_season > 0
    assert named.claim_cuts == ["My QB"]
    assert named.my_season < 0, "your quarterback slot is empty afterwards"


def test_a_claim_with_no_drop_named_still_needs_a_spot():
    client = claimable(capacity=4,
                       claims=[claim_row("Free QB", "Free QB")])
    v, _ = T.grade(client, ["Free QB"], ["Their WR"])
    assert v.claim_known == (("Free QB", ""),)
    assert v.claim_cuts == []
    text = " ".join(T.render_verdict(v, "A League").split())
    assert "no drop named" in text


def test_a_claim_for_somebody_else_still_eats_a_spot():
    """A pending bare add has already spoken for a roster spot. Counting it as
    open would recommend a second move that will not fit."""
    loose = claimable(capacity=4)
    busy = claimable(capacity=4,
                     claims=[claim_row("999", "Some Other Guy")])
    assert T.grade(loose, ["Free QB"], ["Their WR"])[0].claim_cuts == []
    assert T.grade(busy, ["Free QB"], ["Their WR"])[0].claim_cuts == ["Spare"]


def test_the_render_names_the_claim_and_the_drop():
    client = claimable(claims=[claim_row("Free QB", "Free QB", drop="My WR")])
    v, _ = T.grade(client, ["Free QB"], ["Their WR"])
    text = " ".join(T.render_verdict(v, "A League").split())
    assert "already have a claim in for Free QB, dropping My WR" in text
    assert "rather than a guess" in text
