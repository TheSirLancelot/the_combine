"""The daily check, and the gate that keeps it from becoming noise.

Running every morning instead of every Sunday is only tolerable because the
same report does not go out twice. A starter who is out for the season is news
once, not six mornings running, and a channel that repeats itself gets muted --
at which point the one message that mattered is missed too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import bot


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STATE_PATH", tmp_path / "last_post.json")
    return tmp_path / "last_post.json"


def test_the_same_report_does_not_go_out_twice(state):
    signature = ("hurt:1234", "swap:11>22")
    assert bot.already_said("rcl", 2, signature) is False      # first time
    assert bot.already_said("rcl", 2, signature) is True       # same again


def test_a_changed_report_posts(state):
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False
    assert bot.already_said("rcl", 2, ("hurt:1234", "hurt:5678")) is False


def test_the_numbers_moving_is_not_new_news(state):
    """The signature is who and what, never how much. ESPN nudges projections
    through the day, so digesting the rendered text would make every morning
    look like a fresh report."""
    assert bot.already_said("rcl", 2, ("swap:11>22",)) is False
    assert bot.already_said("rcl", 2, ("swap:11>22",)) is True


def test_order_does_not_matter(state):
    assert bot.already_said("rcl", 2, ("a", "b")) is False
    assert bot.already_said("rcl", 2, ("b", "a")) is True


def test_a_new_week_always_posts(state):
    """Week 3's identical-looking report is not week 2's."""
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False
    assert bot.already_said("rcl", 3, ("hurt:1234",)) is False


def test_leagues_are_tracked_separately(state):
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False
    assert bot.already_said("dmwd", 2, ("hurt:1234",)) is False


def test_an_empty_signature_is_never_suppressed(state):
    """No signature means the builder could not say what the report was about.
    Posting twice beats going quiet for the wrong reason."""
    assert bot.already_said("rcl", 2, ()) is False
    assert bot.already_said("rcl", 2, ()) is False


def test_an_unreadable_state_file_posts_rather_than_going_quiet(state, monkeypatch):
    """Best effort in the safe direction: the failure mode of this cache is a
    duplicate message, never a missed one."""
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{ this is not json")
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False


def test_an_unwritable_state_file_does_not_break_the_check(state, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr(type(state), "write_text", boom)
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False
    assert bot.already_said("rcl", 2, ("hurt:1234",)) is False   # still posts


def test_the_check_runs_every_day():
    """It used to be gated to Sunday. Games are played Thursday through Monday,
    so a Sunday-only check misses a Thursday injury and every midweek waiver."""
    assert not hasattr(bot, "CHECK_DAYS")
    assert bot.daily_check.time is not None


def test_startsit_signature_survives_a_projection_change(monkeypatch):
    """The end to end version of the same claim, at the builder."""
    from combine import discord_out

    class Call:
        """Enough of a Call for the signature and the scorecard row."""

        def __init__(self):
            self.bench = type("P", (), {"player_id": "11", "name": "Bench",
                                        "projected": 12.0})()
            self.starter = type("P", (), {"player_id": "22", "name": "Starter",
                                          "projected": 10.0})()
            self.proj_edge = 2.0
            self.needed = 1.8

    monkeypatch.setattr(discord_out, "startsit_embeds", lambda *a, **k: [])
    monkeypatch.setattr(discord_out, "has_news", lambda *a: True)
    monkeypatch.setattr(bot, "_pff", lambda: ({}, {}, True))
    monkeypatch.setattr(bot, "_distribution", lambda: None)
    monkeypatch.setattr(bot.config, "get_league",
                        lambda lg: type("C", (), {"name": "RCL"})())
    monkeypatch.setattr("combine.pipeline.startsit.review",
                        lambda *a, **k: ([Call()], []))
    monkeypatch.setattr("combine.pipeline.lineup.optimal_moves",
                        lambda *a, **k: ([], [], 0.0))
    monkeypatch.setattr("combine.platforms.client_for",
                        lambda lg: type("C", (), {
                            "matchup": lambda self, wk: type(
                                "M", (), {"my_lineup": [], "week": 1})(),
                            "roster_slots": lambda self: {}})())
    assert bot.build_startsit("rcl", 1).signature == ("swap:11>22",)
