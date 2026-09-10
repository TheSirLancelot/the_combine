"""The alert that expires.

Everything else this bot says keeps until you look at it. This does not: once a
game kicks off the lineup is locked, so a starter ruled out at 10am is a zero
you can still avoid at 11:00 and cannot avoid at 13:01. The morning check runs
at 08:30 and structurally cannot see a downgrade that lands after it.

What matters here is the pair of failure modes. Missing the window costs a
starting slot. Firing every fifteen minutes for ninety minutes costs the
channel, and a muted channel misses the next one too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import bot
from combine.platforms import Matchup, ProGame, WeeklyPlayer

NOW = 1_700_000_000_000
MINUTE = 60_000


def player(name, status="OK", slot="RB", team="KC", bye=False):
    return WeeklyPlayer(player_id=name, name=name, team=team, pos="RB", slot=slot,
                        eligible_slots=frozenset({"RB", "BE"}), projected=12.0,
                        status=status, on_bye=bye,
                        game=ProGame(opponent="SF", home=True,
                                     kickoff_ms=NOW + 60 * MINUTE))


class FakeClient:
    week = 3

    def __init__(self, lineup, kickoffs):
        self.lineup = lineup
        self.kickoffs = kickoffs

    def pro_schedule(self, week=None):
        return {team: ProGame(opponent="X", home=True, kickoff_ms=ms)
                for team, ms in self.kickoffs.items()}

    def matchup(self, week=None):
        return Matchup(week=3, home_team="Me", away_team="Them", home_proj=0.0,
                       away_proj=0.0, home_lineup=self.lineup, away_lineup=[],
                       mine="home")


@pytest.fixture
def espn(monkeypatch):
    def install(lineup, kickoffs):
        client = FakeClient(lineup, kickoffs)
        monkeypatch.setattr("combine.platforms.client_for", lambda lg: client)
        return client
    return install


def test_it_warns_about_a_ruled_out_starter_before_the_lock(espn):
    espn([player("Hurt Guy", status="O")], {"KC": NOW + 60 * MINUTE})
    week, players = bot.kickoff_problems("rcl", now_ms=NOW)
    assert week == 3
    assert [p.name for p in players] == ["Hurt Guy"]


def test_a_healthy_starter_is_not_an_alert(espn):
    """Silence is the feature here too. A lineup that is fine produces nothing."""
    espn([player("Fine Guy")], {"KC": NOW + 60 * MINUTE})
    assert bot.kickoff_problems("rcl", now_ms=NOW)[1] == []


def test_a_game_outside_the_window_is_not_checked_yet(espn):
    """Four hours out there is still time for the morning check and for news to
    change. This is the last-chance alert, not a running commentary."""
    espn([player("Hurt Guy", status="O")], {"KC": NOW + 240 * MINUTE})
    assert bot.kickoff_problems("rcl", now_ms=NOW)[1] == []


def test_a_game_already_started_is_too_late_to_warn_about(espn):
    """After kickoff the roster is locked and the alert is just bad news."""
    espn([player("Hurt Guy", status="O")], {"KC": NOW - 5 * MINUTE})
    assert bot.kickoff_problems("rcl", now_ms=NOW)[1] == []


def test_only_the_teams_kicking_off_are_considered(espn):
    """A player whose game is Monday must not be dragged into Sunday's alert."""
    lineup = [player("Sunday Guy", status="O", team="KC"),
              player("Monday Guy", status="O", team="SF")]
    espn(lineup, {"KC": NOW + 60 * MINUTE, "SF": NOW + 2000 * MINUTE})
    assert [p.name for p in bot.kickoff_problems("rcl", now_ms=NOW)[1]] == ["Sunday Guy"]


def test_a_bench_player_is_not_an_emergency(espn):
    """He is not in the lineup, so nothing is lost when his game locks."""
    espn([player("Hurt Bench", status="O", slot="BE")], {"KC": NOW + 60 * MINUTE})
    assert bot.kickoff_problems("rcl", now_ms=NOW)[1] == []


def test_a_starter_on_bye_counts(espn):
    """A bye is not an injury status but it is the same zero."""
    espn([player("Bye Guy", bye=True)], {"KC": NOW + 60 * MINUTE})
    assert [p.name for p in bot.kickoff_problems("rcl", now_ms=NOW)[1]] == ["Bye Guy"]


def test_no_imminent_kickoff_never_touches_the_box_score(espn):
    """The poll runs every 15 minutes all week. On a Wednesday it has to be
    free, or it is 96 ESPN calls a day per league for nothing."""
    client = espn([player("Hurt Guy", status="O")], {"KC": NOW + 2000 * MINUTE})
    called = []
    client.matchup = lambda week=None: called.append(1)
    assert bot.kickoff_problems("rcl", now_ms=NOW)[1] == []
    assert called == []


def test_the_same_alert_does_not_fire_every_poll(tmp_path, monkeypatch):
    """The window is 90 minutes and the poll is 15, so without a gate one
    ruled-out starter is six pings, and six pings is a muted channel."""
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "s.json")
    signature = ("kickoff:123:O",)
    assert bot.already_said("kickoff:rcl", 3, signature) is False
    assert bot.already_said("kickoff:rcl", 3, signature) is True


def test_a_second_player_going_out_is_new_news(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "s.json")
    assert bot.already_said("kickoff:rcl", 3, ("kickoff:123:O",)) is False
    assert bot.already_said("kickoff:rcl", 3,
                            ("kickoff:123:O", "kickoff:456:O")) is False


def test_kickoff_alerts_do_not_suppress_the_morning_check(tmp_path, monkeypatch):
    """Different slugs, so a kickoff alert and the daily report cannot silence
    each other."""
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "s.json")
    assert bot.already_said("kickoff:rcl", 3, ("x",)) is False
    assert bot.already_said("rcl", 3, ("x",)) is False


def test_the_alert_says_what_is_wrong_and_when_it_locks():
    from combine.discord_out import embed_text

    embeds = bot.build_kickoff_alert("rcl", 3, [player("Hurt Guy", status="O")])
    said = embed_text(embeds[0])
    assert "Hurt Guy" in said and "locks" in said
    assert embeds[0].colour.value == 0xE74C3C
