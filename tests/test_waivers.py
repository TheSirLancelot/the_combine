"""Waiver upgrades.

The naive version of this question returned 94 candidates in RCL, so almost
everything here is about what gets excluded and why.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.calibration import Bias, Calibration
from combine.pipeline.waivers import Candidate, _synthetic, render


class FakePool:
    """Stands in for an ESPN pool player."""

    def __init__(self, name, pos, proj, week=1, slots=None, status="ACTIVE",
                 season=100.0):
        self.name = name
        self.position = pos
        self.proTeam = "KC"
        self.playerId = name
        self.injuryStatus = status
        self.eligibleSlots = slots if slots is not None else [pos, "BE"]
        self.projected_total_points = season
        self.stats = {week: {"projected_points": proj}}


def candidate(**kw):
    base = dict(league="rcl", week=1, name="Add", pos="DT", team="IND",
                week_proj=6.6, season_proj=90.0, drop_name="Drop", drop_pos="WR",
                drop_season_proj=100.0, week_gain=1.7, displaces="Starter")
    base.update(kw)
    return Candidate(**base)


def test_a_player_on_bye_is_not_a_candidate():
    """A zero projection is a bye or an inactive, not an upgrade."""
    assert _synthetic(FakePool("Resting", "WR", 0.0), 1) is None


def test_a_ruled_out_player_is_not_a_candidate():
    """Adding someone who cannot play this week achieves nothing."""
    assert _synthetic(FakePool("Hurt", "WR", 12.0, status="OUT"), 1) is None


def test_a_player_with_no_slot_data_is_not_a_candidate():
    assert _synthetic(FakePool("Mystery", "WR", 12.0, slots=[]), 1) is None


def test_a_missing_week_is_not_a_candidate():
    """Asked about week 3, given a player projected only for week 1."""
    assert _synthetic(FakePool("Later", "WR", 12.0, week=1), 3) is None


def test_synthetic_player_is_never_locked():
    """A pool player has not kicked off as far as this code is concerned, or the
    optimizer would refuse to move him in."""
    player = _synthetic(FakePool("Free", "WR", 9.0), 1)
    assert player is not None and not player.locked


def test_season_cost_is_reported_not_folded_into_the_gain():
    """Two currencies. A point this week that costs 90 points of season value is
    usually a bad trade and no single number says so."""
    c = candidate(season_proj=10.0, drop_season_proj=100.0)
    assert c.season_cost == -90.0
    assert c.trades_down
    assert "buys a week and pays for it later" in c.describe()


def test_an_add_that_also_helps_the_season_says_so():
    c = candidate(season_proj=150.0, drop_season_proj=100.0)
    assert not c.trades_down
    assert "gains 50 projected points of season value too" in c.describe()


def test_an_upward_correction_doing_the_work_is_disclosed():
    """Buckner projects below the starter and ranks first only because defensive
    tackles are corrected up 2.5 on 40 observations. Say so."""
    c = candidate(week_gain=1.7, correction=2.49, correction_n=40)
    assert c.correction_carries_it
    text = c.describe()
    assert "corrected UP by 2.5" in text and "40 player-weeks" in text


def test_a_downward_correction_survived_is_confidence_not_a_caveat():
    """He was marked down for his position and still qualifies, which is the
    opposite of a warning."""
    c = candidate(pos="CB", week_gain=1.6, correction=-1.71, correction_n=112)
    assert not c.correction_carries_it
    assert c.clears_despite_correction
    assert "marked DOWN 1.7" in c.describe()


def test_no_correction_means_no_note_at_all():
    c = candidate(pos="DE", week_gain=1.6, correction=0.0)
    text = c.describe()
    assert "NOTE" not in text and "marked DOWN" not in text


def test_nothing_available_says_so_plainly():
    text = render([], "A League", 1)
    assert "Nobody on the wire improves the lineup" in text
    assert "normal answer" in text


def test_calibration_can_reverse_a_raw_comparison():
    """The whole reason calibration comes first: on raw numbers the corner beats
    the tackle, and corrected he does not."""
    cal = Calibration("rcl", 2025, {
        "CB": Bias("CB", 112, -1.71, 0.51),
        "DT": Bias("DT", 40, 2.49, 0.98),
    })
    assert 10.0 > 6.6                                  # raw
    assert cal.adjust("CB", 10.0) < cal.adjust("DT", 6.6) + 0.8
    assert round(cal.adjust("DT", 6.6), 2) == 9.09


# --- drops you are actually allowed to make ---------------------------------

class FakeClient:
    """Enough of a league client for `find` to run against a fixed roster."""

    slug = "rcl"
    week = 1

    class _League:
        def __init__(self, pool):
            self._pool = pool

        def free_agents(self, size=350):
            return self._pool

    def __init__(self, lineup, pool):
        self.lineup = lineup
        self.league = self._League(pool)

    def matchup(self, week=None):
        from combine.platforms import Matchup

        return Matchup(week=1, home_team="Me", away_team="Them", home_proj=0.0,
                       away_proj=0.0, home_lineup=self.lineup, away_lineup=[],
                       mine="home")

    def roster_slots(self):
        return {"WR": 1, "DT": 1}


def rostered(name, pos, slot, proj=5.0, started=False):
    from combine.platforms import ProGame, WeeklyPlayer

    past, future = 1_000_000_000_000, 4_000_000_000_000
    return WeeklyPlayer(
        player_id=name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset({pos, "BE"}), projected=proj,
        played=started,
        game=ProGame(opponent="SF", home=True,
                     kickoff_ms=past if started else future))


def find_against(lineup, pool, values):
    from combine.pipeline.waivers import find

    return find(FakeClient(lineup, pool), 1, season_value=values, limit=5)


def test_a_player_who_already_played_is_not_offered_as_a_drop():
    """The bug this exists for. It told him to drop Rashid Shaheed on a Monday,
    after Shaheed had played, which the platform will not allow."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Cheap But Played", "WR", "BE", 4.0, started=True),
              rostered("Droppable", "WR", "BE", 5.0)]
    found = find_against(lineup, [FakePool("Big Add", "DT", 14.0)],
                         {"Cheap But Played": 20.0, "Droppable": 60.0,
                          "Starter": 200.0})
    assert found, "the add itself should still be found"
    assert found[0].drop_name == "Droppable"


def test_it_names_the_drop_you_would_rather_make_and_prices_it():
    """Silently substituting a more expensive drop would hide the cost."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Cheap But Played", "WR", "BE", 4.0, started=True),
              rostered("Droppable", "WR", "BE", 5.0)]
    found = find_against(lineup, [FakePool("Big Add", "DT", 14.0)],
                         {"Cheap But Played": 20.0, "Droppable": 60.0,
                          "Starter": 200.0})
    c = found[0]
    assert c.blocked_name == "Cheap But Played"
    assert c.blocked_cost == 40.0              # 60 given up instead of 20
    note = c.blocked_note()
    assert "cannot be dropped until the weekly reset" in note
    assert "40 more points" in note


def test_no_note_when_the_best_drop_is_also_legal():
    """Most of the time. A caveat that fires every week stops being read."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Droppable", "WR", "BE", 5.0)]
    found = find_against(lineup, [FakePool("Big Add", "DT", 14.0)],
                         {"Droppable": 20.0, "Starter": 200.0})
    assert found[0].blocked_name is None
    assert found[0].blocked_note() == ""


def test_a_fully_locked_roster_says_so_instead_of_recommending_the_impossible():
    lineup = [rostered("Starter", "WR", "WR", 8.0, started=True),
              rostered("Bench", "WR", "BE", 5.0, started=True)]
    found = find_against(lineup, [FakePool("Big Add", "DT", 14.0)],
                         {"Bench": 20.0, "Starter": 200.0})
    assert found[0].drop_locked is True
    assert "has to wait for the weekly reset" in found[0].blocked_note()


def test_blocked_cost_is_never_negative():
    """The blocked player is the cheapest by construction, so anyone else costs
    at least as much. A negative would mean the ordering broke."""
    c = candidate(blocked_name="X", blocked_pos="WR", blocked_season_proj=150.0,
                  drop_season_proj=100.0)
    assert c.blocked_cost == 0.0


def test_an_empty_starting_slot_accepts_anyone():
    """A slot with nobody in it is beaten by any projection at all. This used to
    default to infinity, so the tool reported an empty wire instead."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Droppable", "WR", "BE", 5.0)]      # nobody at DT
    found = find_against(lineup, [FakePool("Weak DT", "DT", 3.0)],
                         {"Droppable": 20.0, "Starter": 200.0})
    assert [c.name for c in found] == ["Weak DT"]


def test_the_cheap_filter_still_excludes_a_player_who_cannot_help():
    """The other half of that fix: a filled slot must still reject someone
    projected below the man already in it."""
    lineup = [rostered("Starter", "WR", "WR", 20.0),
              rostered("DT Starter", "DT", "DT", 12.0),
              rostered("Droppable", "WR", "BE", 5.0)]
    found = find_against(lineup, [FakePool("Worse DT", "DT", 2.0)],
                         {"Droppable": 20.0, "Starter": 200.0,
                          "DT Starter": 180.0})
    assert found == []


# --- kickers and defenses ---------------------------------------------------

def test_kickers_are_never_offered():
    """Measured, not assumed. Across 245 kicker player-weeks in 2025 the
    projections have a standard deviation of 0.4 against an outcome spread of
    11.4, and picking the higher-projected of two kickers scored more 50.0% of
    the time across 39,568 pairs. Every kicker recommendation would be noise
    wearing a number."""
    from combine.pipeline.waivers import _synthetic

    kicker = FakePool("Some Kicker", "K", 8.6, slots=["K", "BE"])
    assert _synthetic(kicker, 1) is None


def test_defenses_are_offered():
    """+0.258 correlation and the higher projection wins 56.9% of pairs, which
    is the same signal as IDP, a family the tool already acts on."""
    from combine.pipeline.waivers import _synthetic

    dst = FakePool("Titans D/ST", "D/ST", 6.8, slots=["D/ST", "BE"])
    assert _synthetic(dst, 1) is not None


def test_adding_a_defense_drops_the_defense_you_have():
    """Nobody carries two defenses. Dropping a fringe receiver instead compared
    a defense's season projection against a receiver's and reported a 92 point
    season loss for a one point weekly gain: true, and answering a question
    nobody asked."""
    lineup = [rostered("My D/ST", "D/ST", "D/ST", 5.3),
              rostered("Fringe WR", "WR", "BE", 1.0),
              rostered("Starter", "WR", "WR", 12.0)]
    client = FakeClient(lineup, [FakePool("Better D/ST", "D/ST", 9.0,
                                          slots=["D/ST", "BE"], season=60.0)])
    client.roster_slots = lambda: {"WR": 1, "D/ST": 1}
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"My D/ST": 100.0, "Fringe WR": 5.0,
                                          "Starter": 200.0}, limit=5)
    assert found, "the defense should be a candidate"
    assert found[0].drop_name == "My D/ST"
    assert found[0].season_cost == -40.0   # 60 for his season against 100 for yours


def test_a_streamed_drop_is_not_reported_as_a_lock():
    """The cheapest player is a fringe receiver, but the defense is the right
    drop and nothing here is locked. Saying "his game has started" would be a
    false explanation of a deliberate choice."""
    lineup = [rostered("My D/ST", "D/ST", "D/ST", 5.3),
              rostered("Fringe WR", "WR", "BE", 1.0),
              rostered("Starter", "WR", "WR", 12.0)]
    client = FakeClient(lineup, [FakePool("Better D/ST", "D/ST", 9.0,
                                          slots=["D/ST", "BE"], season=60.0)])
    client.roster_slots = lambda: {"WR": 1, "D/ST": 1}
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"My D/ST": 100.0, "Fringe WR": 5.0,
                                          "Starter": 200.0}, limit=5)
    assert found[0].blocked_name is None
    assert found[0].blocked_note() == ""


def test_a_streamed_add_still_respects_the_lock():
    """A defense that has already played cannot be dropped either."""
    lineup = [rostered("My D/ST", "D/ST", "D/ST", 5.3, started=True),
              rostered("Fringe WR", "WR", "BE", 1.0),
              rostered("Starter", "WR", "WR", 12.0)]
    client = FakeClient(lineup, [FakePool("Better D/ST", "D/ST", 9.0,
                                          slots=["D/ST", "BE"])])
    client.roster_slots = lambda: {"WR": 1, "D/ST": 1}
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"My D/ST": 100.0, "Fringe WR": 5.0,
                                          "Starter": 200.0}, limit=5)
    assert found[0].drop_name != "My D/ST"


# --- the IR stash -----------------------------------------------------------

class IRClient(FakeClient):
    """FakeClient that also reports slot counts, the way ESPN does.

    The bench defaults to exactly full, because an open roster spot is itself a
    free add and would otherwise mask whatever the test is actually about.
    Tests that want an open spot pass `bench`.
    """

    def __init__(self, lineup, pool, ir_slots=1, bench=None):
        super().__init__(lineup, pool)
        active = [p for p in lineup if p.slot != "IR"]
        if bench is None:
            bench = max(0, len(active) - 2)      # 2 starting slots below
        settings = type("S", (), {"position_slot_counts":
                                  {"WR": 1, "DT": 1, "BE": bench,
                                   "IR": ir_slots}})()
        self.league.settings = settings


def hurt(name, pos, slot, proj=5.0, status="O"):
    from combine.platforms import ProGame, WeeklyPlayer

    return WeeklyPlayer(
        player_id=name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset({pos, "BE", "IR"}), projected=proj,
        status=status,
        game=ProGame(opponent="SF", home=True, kickoff_ms=4_000_000_000_000))


def test_an_out_player_can_be_stashed():
    from combine.pipeline.waivers import stashable

    lineup = [hurt("Hurt Guy", "WR", "BE"), rostered("Fine Guy", "WR", "WR")]
    assert [p.name for p in stashable(lineup)] == ["Hurt Guy"]


def test_questionable_and_doubtful_are_not_ir_eligible():
    """ESPN does not accept them, and a stash it refuses is worse than one we
    never suggested."""
    from combine.pipeline.waivers import stashable

    lineup = [hurt("Q Guy", "WR", "BE", status="Q"),
              hurt("D Guy", "WR", "BE", status="D")]
    assert stashable(lineup) == []


def test_someone_already_on_ir_is_not_stashed_again():
    from combine.pipeline.waivers import stashable

    assert stashable([hurt("Already", "WR", "IR")]) == []


def test_ir_room_counts_what_is_free_not_what_exists():
    from combine.pipeline.waivers import ir_room

    lineup = [hurt("On IR", "WR", "IR"), rostered("Fine", "WR", "WR")]
    client = IRClient(lineup, [], ir_slots=2)
    assert ir_room(client, lineup) == 1


def test_a_league_with_no_ir_slots_has_no_room():
    from combine.pipeline.waivers import ir_room

    client = IRClient([], [], ir_slots=0)
    assert ir_room(client, []) == 0


def test_a_stash_makes_the_add_free():
    """The whole point. Nothing leaves the roster, so there is no season value
    given up and the season column is the add's own worth."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              hurt("Hurt Guy", "DT", "BE"),
              rostered("Cheap", "WR", "BE", 2.0)]
    client = IRClient(lineup, [FakePool("Big Add", "DT", 14.0, season=120.0)])
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Cheap": 10.0, "Starter": 200.0,
                                          "Hurt Guy": 150.0}, limit=5)
    assert found, "the add should be found"
    c = found[0]
    assert c.is_stash
    assert c.stash_name == "Hurt Guy"
    assert c.season_cost == 120.0          # his own value, nothing given up
    assert c.trades_down is False


def test_no_ir_room_means_a_normal_drop():
    """An injured player with nowhere to put him is just an injured player."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              hurt("Hurt Guy", "DT", "BE"),
              rostered("Cheap", "WR", "BE", 2.0)]
    client = IRClient(lineup, [FakePool("Big Add", "DT", 14.0, season=120.0)],
                      ir_slots=0)
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Cheap": 10.0, "Starter": 200.0,
                                          "Hurt Guy": 150.0}, limit=5)
    assert found[0].is_stash is False
    assert found[0].stash_name is None


def test_a_bench_player_is_stashable_too():
    """He asked for this specifically: the injured player does not have to be
    in the lineup for the slot to be worth using."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              hurt("Benched And Out", "DT", "BE")]
    client = IRClient(lineup, [FakePool("Big Add", "DT", 14.0)])
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Starter": 200.0,
                                          "Benched And Out": 150.0}, limit=5)
    assert found[0].stash_name == "Benched And Out"


def test_the_stash_note_stands_on_its_own():
    """An unused IR slot is a roster spot he already owns, worth saying even
    when nothing on the wire clears the bar."""
    from combine.pipeline.waivers import stash_note

    lineup = [hurt("Hurt Guy", "WR", "BE"), rostered("Fine", "WR", "WR")]
    said = stash_note(IRClient(lineup, []), lineup)
    assert "Hurt Guy" in said and "without dropping anybody" in said


def test_no_note_when_there_is_nothing_to_stash():
    from combine.pipeline.waivers import stash_note

    lineup = [rostered("Fine", "WR", "WR")]
    assert stash_note(IRClient(lineup, []), lineup) == ""


def test_no_note_when_the_ir_slots_are_full():
    from combine.pipeline.waivers import stash_note

    lineup = [hurt("On IR", "WR", "IR"), hurt("Also Hurt", "WR", "BE")]
    assert stash_note(IRClient(lineup, [], ir_slots=1), lineup) == ""


def test_suspended_players_are_not_ir_eligible():
    """ESPN's help says so explicitly. The first version of this assumed a
    suspension was an absence like any other; it is not, to ESPN."""
    from combine.pipeline.waivers import stashable

    assert stashable([hurt("Banned", "WR", "BE", status="SUSP")]) == []
    assert stashable([hurt("Banned", "WR", "BE", status="SSPD")]) == []


def test_out_and_ir_are_eligible():
    from combine.pipeline.waivers import stashable

    for status in ("O", "OUT", "IR"):
        assert [p.name for p in
                stashable([hurt("Guy", "WR", "BE", status=status)])] == ["Guy"]


def test_a_healthy_player_in_an_ir_slot_invalidates_the_roster():
    """Not advice. ESPN blocks lineup changes and waiver claims until he is
    moved, so every other recommendation is unactionable while this is true."""
    from combine.pipeline.waivers import ir_invalid

    lineup = [hurt("Recovered", "WR", "IR", status="OK"),
              rostered("Fine", "WR", "WR")]
    assert [p.name for p in ir_invalid(lineup)] == ["Recovered"]


def test_questionable_in_an_ir_slot_is_allowed_to_stay():
    """ESPN's own rule: a player already in the slot may stay when he improves
    to Questionable or Doubtful. Only losing the designation entirely forces
    the move."""
    from combine.pipeline.waivers import ir_invalid

    assert ir_invalid([hurt("Improving", "WR", "IR", status="Q")]) == []
    assert ir_invalid([hurt("Improving", "WR", "IR", status="D")]) == []


def test_the_blocking_problem_outranks_the_free_spot():
    """An invalid roster cannot make the add, so leading with the free slot
    would be advice he cannot take."""
    from combine.pipeline.waivers import stash_note

    lineup = [hurt("Recovered", "WR", "IR", status="OK"),
              hurt("Still Out", "WR", "BE", status="O")]
    said = stash_note(IRClient(lineup, [], ir_slots=2), lineup)
    assert "INVALID" in said and "Recovered" in said
    assert "frees a roster spot" not in said


# --- ordering ---------------------------------------------------------------

def test_season_value_breaks_a_tie_the_week_cannot_settle():
    """The bug this fixes, with the real numbers. Gibbens (+4.6 week, +81.8
    season) ranked above Elliss (+4.0, +205.8), trading 124 points of season for
    six tenths of a Sunday, and both were free adds so nothing was bought."""
    gibbens = candidate(name="Gibbens", week_gain=4.63, season_proj=81.8,
                        drop_season_proj=0.0)
    elliss = candidate(name="Elliss", week_gain=4.04, season_proj=205.8,
                       drop_season_proj=0.0)
    assert min([gibbens, elliss], key=lambda c: c.rank()).name == "Elliss"


def test_a_real_weekly_edge_still_wins():
    """Season value is a tiebreak, not the ranking. A candidate a full measured
    edge better this week goes first however the season looks."""
    now = candidate(name="Now", week_gain=6.5, season_proj=50.0,
                    drop_season_proj=0.0)
    later = candidate(name="Later", week_gain=4.0, season_proj=300.0,
                      drop_season_proj=0.0)
    assert min([now, later], key=lambda c: c.rank()).name == "Now"


def test_the_band_is_the_measured_floor_not_a_round_number():
    """MIN_EDGE is the point below which a projection gap does not predict who
    scores more. Two candidates inside it are indistinguishable for the week,
    which is what makes preferring season value free."""
    import inspect

    from combine.pipeline.lineup import MIN_EDGE
    from combine.pipeline.waivers import Candidate

    assert MIN_EDGE == inspect.signature(Candidate.rank).parameters["band"].default


def test_a_win_now_add_is_not_buried():
    """Converting season value to points a week and adding would make the
    season term three times the weekly one with seventeen weeks left, which
    stops being a tiebreak and becomes the whole ranking."""
    best_week = candidate(name="Best Week", week_gain=4.63, season_proj=81.8,
                          drop_season_proj=0.0)
    others = [candidate(name=f"Season {i}", week_gain=4.0,
                        season_proj=200.0 + i, drop_season_proj=0.0)
              for i in range(3)]
    order = [c.name for c in sorted([best_week, *others], key=lambda c: c.rank())]
    assert order.index("Best Week") <= 3       # still on the first screen


def test_a_candidate_that_costs_the_season_sorts_last_within_its_band():
    costly = candidate(name="Costly", week_gain=4.2, season_proj=40.0,
                       drop_season_proj=160.0)      # -120 season
    free = candidate(name="Free", week_gain=4.0, season_proj=90.0,
                     drop_season_proj=0.0)
    assert min([costly, free], key=lambda c: c.rank()).name == "Free"


# --- pending claims and open spots ------------------------------------------

class TxClient(IRClient):
    """IRClient that also answers transactions(), the way ESPN does."""

    def __init__(self, lineup, pool, pending=(), **kw):
        super().__init__(lineup, pool, **kw)
        self.cfg = type("C", (), {"team_id": 6})()
        team = type("T", (), {"team_id": 6})()
        self.league.transactions = lambda types=None: [
            type("Tx", (), {
                "status": "PENDING", "team": team, "type": "WAIVER",
                "items": [type("I", (), {"type": "ADD", "playerId": pid,
                                         "player": name})()],
            })() for pid, name in pending]


def test_a_player_already_claimed_is_not_recommended_again():
    """He has a claim in on Elliss. Offering Elliss is noise."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Cheap", "WR", "BE", 2.0)]
    pool = [FakePool("Claimed Guy", "DT", 14.0), FakePool("Other Guy", "DT", 13.0)]
    client = TxClient(lineup, pool, pending=[("Claimed Guy", "Claimed Guy")])
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Cheap": 10.0, "Starter": 200.0})
    assert "Claimed Guy" not in [c.name for c in found]
    assert "Other Guy" in [c.name for c in found]


def test_a_pending_claim_does_not_count_its_spot_as_open_twice():
    """The spot is already spoken for. Counting it as open recommends a second
    add that will not fit."""
    from combine.pipeline.waivers import roster_room

    lineup = [rostered("Starter", "WR", "WR", 8.0)]
    # capacity is WR 1 + DT 1 + BE 2 = 4, against one rostered player
    client = TxClient(lineup, [], bench=2)
    assert roster_room(client, lineup, {}) == 3
    assert roster_room(client, lineup, {"1": "Someone"}) == 2


def test_an_open_roster_spot_makes_the_add_free():
    """Same economics as an IR stash, and it needs no move at all."""
    lineup = [rostered("Starter", "WR", "WR", 8.0)]
    client = TxClient(lineup, [FakePool("Big Add", "DT", 14.0, season=120.0)],
                      bench=2)
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Starter": 200.0})
    assert found[0].is_free
    assert found[0].free_via == "you have an open roster spot"
    assert found[0].season_cost == 120.0
    assert found[0].drop_name == ""


def test_an_open_spot_is_preferred_over_stashing():
    """Both are free, but one requires no move."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              hurt("Hurt Guy", "DT", "BE")]
    client = TxClient(lineup, [FakePool("Big Add", "DT", 14.0)], bench=3)
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Starter": 200.0, "Hurt Guy": 150.0})
    assert found[0].is_free and not found[0].is_stash


def test_a_full_roster_with_no_ir_room_still_requires_a_drop():
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              rostered("Cheap", "WR", "BE", 2.0)]
    client = TxClient(lineup, [FakePool("Big Add", "DT", 14.0, season=120.0)],
                      ir_slots=0)
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Cheap": 10.0, "Starter": 200.0})
    assert not found[0].is_free
    assert found[0].drop_name == "Cheap"


def test_the_pending_note_says_instead_not_as_well():
    """The rows below a pending claim are the fallback if it fails, not a
    second move to make alongside it."""
    from combine.pipeline.waivers import pending_note

    said = pending_note({"1": "Christian Elliss"})
    assert "Christian Elliss" in said
    assert "INSTEAD" in said
    assert "who else bid" in said


def test_no_pending_claims_means_no_note():
    from combine.pipeline.waivers import pending_note

    assert pending_note({}) == ""


def test_a_transactions_call_that_fails_does_not_break_the_view():
    """Bookkeeping must never take the waiver view down."""
    from combine.pipeline.waivers import pending_adds

    class Broken:
        cfg = type("C", (), {"team_id": 6})()
        league = type("L", (), {"transactions": staticmethod(
            lambda types=None: (_ for _ in ()).throw(RuntimeError("500")))})()

    assert pending_adds(Broken()) == {}


def test_another_team_s_pending_claim_is_ignored():
    """ESPN only shows our own, but the filter should not depend on that."""
    from combine.pipeline.waivers import pending_adds

    client = TxClient([], [], pending=[("9", "Someone Else")])
    client.league.transactions = lambda types=None: [
        type("Tx", (), {
            "status": "PENDING",
            "team": type("T", (), {"team_id": 99})(),
            "items": [type("I", (), {"type": "ADD", "playerId": "9",
                                     "player": "Someone Else"})()],
        })()]
    assert pending_adds(client) == {}


def test_a_player_stashed_on_ir_is_not_offered_as_a_drop():
    """He can technically be dropped, and ESPN depresses an injured player's
    season projection, which together made Myles Garrett look like the cheapest
    drop on the roster the day after he was stashed. Stashing someone is what
    you do when you want to keep him."""
    lineup = [rostered("Starter", "WR", "WR", 8.0),
              hurt("Stashed Star", "DT", "IR"),
              rostered("Fringe", "WR", "BE", 1.0)]
    client = TxClient(lineup, [FakePool("Big Add", "DT", 14.0)], ir_slots=1)
    from combine.pipeline.waivers import find

    found = find(client, 1, season_value={"Stashed Star": 20.0,   # looks cheap
                                          "Fringe": 50.0, "Starter": 200.0})
    assert found[0].drop_name == "Fringe"
