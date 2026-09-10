"""Weeks you cannot fill, before they arrive.

Feasibility only, and the reason that restriction matters is that the obvious
version of this feature is a prediction: "how good will your lineup be in week
7". ESPN does not publish a week 7 projection in week 2, so that number would
have to be invented, and inventing numbers is the thing this project has twice
measured and thrown away. Whether a roster can legally fill its slots is
arithmetic on the schedule, and that is all this claims.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.lookahead import look
from combine.platforms import Matchup, ProGame, WeeklyPlayer


def player(name, pos, team, eligible=None):
    return WeeklyPlayer(player_id=name, name=name, team=team, pos=pos, slot=pos,
                        eligible_slots=frozenset(eligible or {pos, "BE"}),
                        projected=10.0)


class FakeClient:
    week = 1

    def __init__(self, roster, byes=None, slots=None):
        self.roster = roster
        self.byes = byes or {}          # week -> set of teams NOT playing
        self.slots = slots or {"RB": 1, "WR": 1}

    def roster_slots(self):
        return self.slots

    def matchup(self, week=None):
        return Matchup(week=1, home_team="Me", away_team="Them", home_proj=0.0,
                       away_proj=0.0, home_lineup=self.roster, away_lineup=[],
                       mine="home")

    def pro_schedule(self, week=None):
        teams = {"KC", "SF", "BUF", "DAL"} - set(self.byes.get(week, ()))
        return {t: ProGame(opponent="X", home=True, kickoff_ms=0) for t in teams}


def test_a_full_roster_is_fine():
    weeks = look(FakeClient([player("A", "RB", "KC"), player("B", "WR", "SF")]),
                 weeks=2)
    assert [w.short for w in weeks] == [0, 0]
    assert all(w.ok for w in weeks)


def test_a_bye_that_empties_a_slot_is_flagged():
    """The whole point. Week 3 has nobody who can fill WR."""
    client = FakeClient([player("A", "RB", "KC"), player("B", "WR", "SF")],
                        byes={3: {"SF"}})
    weeks = {w.week: w for w in look(client, weeks=3)}
    assert weeks[2].ok
    assert weeks[3].short == 1
    assert weeks[3].on_bye == ["B"]


def test_a_bye_with_cover_is_not_flagged():
    """Two receivers, one on bye, one slot. Nothing is wrong and saying so
    would train him to ignore the warning."""
    client = FakeClient([player("A", "RB", "KC"), player("B", "WR", "SF"),
                         player("C", "WR", "BUF")], byes={3: {"SF"}})
    weeks = {w.week: w for w in look(client, weeks=3)}
    assert weeks[3].ok
    assert weeks[3].on_bye == ["B"]          # still reported, just not a problem


def test_overlapping_eligibility_is_solved_not_counted():
    """A flex is why this uses the optimizer. Counting per slot would say the
    lone back covers both RB and RB/WR, which is one player in two places."""
    client = FakeClient([player("A", "RB", "KC", {"RB", "RB/WR", "BE"})],
                        slots={"RB": 1, "RB/WR": 1})
    weeks = look(client, weeks=1)
    assert weeks[0].slots == 2
    assert weeks[0].fillable == 1
    assert weeks[0].short == 1


def test_it_looks_past_the_current_week_not_at_it():
    """This week already has a whole view of its own."""
    client = FakeClient([player("A", "RB", "KC"), player("B", "WR", "SF")])
    assert [w.week for w in look(client, weeks=3)] == [2, 3, 4]


def test_it_stops_at_the_end_of_the_season():
    """Asking for week 19 raises rather than answering, and running off the end
    must not take the view down."""
    class EndOfSeason(FakeClient):
        def pro_schedule(self, week=None):
            if week > 3:
                raise RuntimeError("no such week")
            return super().pro_schedule(week)

    client = EndOfSeason([player("A", "RB", "KC"), player("B", "WR", "SF")])
    assert [w.week for w in look(client, weeks=6)] == [2, 3]


def test_the_description_names_the_players_on_bye():
    client = FakeClient([player("A", "RB", "KC"), player("B", "WR", "SF")],
                        byes={2: {"SF"}})
    said = look(client, weeks=1)[0].describe()
    assert "week 2" in said and "B" in said and "1 slot" in said
