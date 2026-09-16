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

from combine.pipeline.compare import (
    FREE,
    MINE,
    Detail,
    Side,
    compare,
    detail,
    espn_history,
    render_detail,
    resolve,
    schedule_ahead,
    stat_rows,
)
from combine.pipeline.usage import Usage
from combine.platforms import Matchup, ProGame, WeeklyPlayer

FUTURE = 4_000_000_000_000


def player(name, pos, slot="BE", proj=10.0, eligible=None, pid=None):
    return WeeklyPlayer(
        player_id=pid or name, name=name, team="KC", pos=pos, slot=slot,
        eligible_slots=frozenset(eligible or {pos, "BE"}), projected=proj,
        game=ProGame(opponent="SF", home=True, kickoff_ms=FUTURE))


class PoolPlayer:
    def __init__(self, name, pos, proj, season=100.0, pid=None):
        self.name = name
        self.position = pos
        self.proTeam = "KC"
        self.playerId = pid or name
        self.injuryStatus = "ACTIVE"
        self.eligibleSlots = [pos, "BE"]
        self.projected_total_points = season
        # Every week, so a fixture can pick one without the pool
        # quietly emptying.
        self.stats = {w: {"projected_points": proj} for w in range(1, 19)}


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


# --- the deeper comparison --------------------------------------------------
#
# The one thing worth stating twice: ESPN publishes a projection for the
# CURRENT week and a season total, and nothing beyond. So the week-by-week
# half of this is ACTUALS, and the tests below pin that down rather than
# leaving room for a future forecast to be quietly invented.

class Info:
    """What `league.player_info` hands back."""

    def __init__(self, pid, stats, season):
        self.playerId = pid
        self.stats = stats
        self.projected_total_points = season


class Deeper(Client):
    """A client that can also answer the schedule and the player lookup."""

    week = 3

    def __init__(self, mine, others=(), pool=(), info=(), schedule=None):
        super().__init__(mine, others, pool)
        self.league.player_info = lambda playerId: list(info)
        self._schedule = schedule or {}

    def pro_schedule(self, week=None):
        return self._schedule.get(week, {})


def game(opponent="SF", home=True):
    return ProGame(opponent=opponent, home=home, kickoff_ms=FUTURE)


def test_history_keeps_played_weeks_and_the_season_projection():
    client = Deeper([], info=[Info(1, {
        0: {"points": 999.0},          # season bucket, not a week
        1: {"points": 21.4},
        2: {"points": 8.0},
        3: {"points": 0.0},            # this week, not played yet
    }, 268.5)])
    got = espn_history(client, ["1"], through=3)
    assert got["1"]["weeks"] == {1: 21.4, 2: 8.0}
    assert got["1"]["season"] == 268.5


def test_history_skips_ids_espn_cannot_have():
    """D/ST ids are strings like 'KC', and player_info would raise on them."""
    assert espn_history(Deeper([]), ["KC"], through=3) == {}


def test_history_survives_a_failed_lookup():
    client = Deeper([])

    def boom(playerId):
        raise RuntimeError("espn is down")

    client.league.player_info = boom
    assert espn_history(client, ["1"], through=3) == {}


def test_the_schedule_ahead_names_a_bye():
    client = Deeper([], schedule={
        3: {"KC": game("SF", home=True)},
        4: {},                                  # KC on bye
        5: {"KC": game("DEN", home=False)},
    })
    assert schedule_ahead(client, "KC", 3, weeks=3) == [
        (3, "vs SF"), (4, "BYE"), (5, "@ DEN")]


def test_a_player_with_no_team_has_no_schedule():
    assert schedule_ahead(Deeper([]), None, 3) == []


def usage_for(pff_id, pos_rows):
    return Usage(pff_id=pff_id, name="x", season=2025, rows=pos_rows)


RB_ROWS = {
    "rushing": {"player_game_count": 10, "attempts": 150, "receptions": 20,
                "yco_attempt": 3.1, "breakaway_percent": 12.0,
                "grades_offense": 85.0},
    "receiving": {"player_game_count": 10, "routes": 200},
}
RB_ROWS_B = {
    "rushing": {"player_game_count": 10, "attempts": 100, "receptions": 30,
                "yco_attempt": 2.4, "breakaway_percent": 6.0,
                "grades_offense": 70.0},
    "receiving": {"player_game_count": 10, "routes": 260},
}


def test_two_backs_compare_stat_by_stat():
    a = Side(player("A", "RB", slot="RB"), MINE)
    b = Side(player("B", "RB", slot="RB"), FREE)
    usage = {11: usage_for(11, RB_ROWS), 22: usage_for(22, RB_ROWS_B)}
    rows = stat_rows(a, b, usage, {"A": 11, "B": 22})
    labels = [label for label, _va, _vb, _dp in rows]
    assert labels == ["touch/g", "route/g", "yco/att", "brk%", "grade"]
    seen = {label: (va, vb) for label, va, vb, _dp in rows}
    assert seen["yco/att"] == (3.1, 2.4)


def test_a_back_and_a_receiver_get_no_table_at_all():
    """Not a formatting nicety. Lining the two lists up by index would put one
    man's yards per route run beside the other's yards after contact."""
    a = Side(player("A", "RB", slot="RB"), MINE)
    b = Side(player("B", "WR", slot="WR"), FREE)
    usage = {11: usage_for(11, RB_ROWS), 22: usage_for(22, RB_ROWS_B)}
    assert stat_rows(a, b, usage, {"A": 11, "B": 22}) == []


def test_a_player_pff_has_never_charted_gets_no_table():
    a = Side(player("A", "RB", slot="RB"), MINE)
    b = Side(player("B", "RB", slot="RB"), FREE)
    usage = {11: usage_for(11, RB_ROWS)}
    assert stat_rows(a, b, usage, {"A": 11}) == []


def test_the_season_difference_is_signed_towards_the_incoming_player():
    d = Detail(result=None, form={}, schedule={}, stats=[],
               season_a=200.0, season_b=260.0)
    assert d.season_delta == 60.0
    assert Detail(result=None, form={}, schedule={}, stats=[],
                  season_a=260.0, season_b=200.0).season_delta == -60.0


# ESPN ids are numeric, and `espn_history` only asks about ids that could be
# real ones, so the fixture uses numbers rather than names here.
IDS = {"1": 11, "2": 22}


def _deeper():
    mine = [player("Weak RB", "RB", slot="RB", proj=4.0, pid="1")]
    return Deeper(
        mine,
        pool=[PoolPlayer("Strong RB", "RB", 15.0, season=300.0, pid="2")],
        info=[Info(1, {1: {"points": 3.0}, 2: {"points": 5.0}}, 120.0),
              Info(2, {1: {"points": 18.0}, 2: {"points": 22.0}}, 300.0)],
        schedule={3: {"KC": game("SF")}, 4: {}, 5: {"KC": game("DEN", home=False)},
                  6: {"KC": game("LV")}, 7: {"KC": game("LAC")}})


def test_detail_gathers_form_season_schedule_and_stats():
    client = _deeper()
    result, err = compare(client, "Weak RB", "Strong RB", week=3)
    assert not err
    usage = {11: usage_for(11, RB_ROWS), 22: usage_for(22, RB_ROWS_B)}
    d = detail(client, result, usage, IDS, 2026)
    assert d.form["1"] == {1: 3.0, 2: 5.0}
    assert d.form["2"] == {1: 18.0, 2: 22.0}
    assert d.season_a == 120.0 and d.season_b == 300.0
    assert d.season_delta == 180.0
    assert ("BYE" in [label for _w, label in d.schedule["1"]])
    assert next(label for label, *_rest in d.stats) == "touch/g"


def test_the_rendered_detail_says_what_it_has_and_what_it_cannot_have():
    client = _deeper()
    result, _ = compare(client, "Weak RB", "Strong RB", week=3)
    usage = {11: usage_for(11, RB_ROWS), 22: usage_for(22, RB_ROWS_B)}
    text = render_detail(detail(client, result, usage, IDS, 2026))
    assert "FORM so far, points actually scored" in text
    assert "no weekly" in text and "not invented" in text
    assert "bye inside this window" in text
    assert "PFF, same position" in text


def test_the_rendered_detail_explains_a_missing_pff_table():
    client = Deeper([player("My RB", "RB", slot="RB", proj=9.0)],
                    pool=[PoolPlayer("Some WR", "WR", 11.0)])
    result, _ = compare(client, "My RB", "Some WR", week=3)
    text = render_detail(detail(client, result, {}, {}, 2026))
    assert "different position groups" in text
