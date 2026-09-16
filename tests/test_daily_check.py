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


def _deal(give_id: str, get_id: str, season: float):
    from combine.pipeline.trades import Deal
    from combine.platforms import WeeklyPlayer

    def man(pid, name, pos):
        return WeeklyPlayer(player_id=pid, name=name, team="KC", pos=pos,
                            slot="BE")

    return Deal(give=man(give_id, f"Give {give_id}", "RB"),
                get=man(get_id, f"Get {get_id}", "QB"), partner="Them",
                my_season=season, their_season=40.0, my_week=0.5,
                their_week=-0.4, partner_thin="LB", partner_deep="WR")


def _as_espn(monkeypatch):
    monkeypatch.setattr(bot.config, "get_league",
                        lambda lg: type("C", (), {"name": "RCL",
                                                  "platform": "espn"})())


def test_a_trade_is_news_and_says_which_players_it_is_about(monkeypatch):
    _as_espn(monkeypatch)
    monkeypatch.setattr("combine.pipeline.trades.for_league",
                        lambda lg, **k: [_deal("11", "22", 32.0)])
    report = bot.build_trades("rcl")
    assert report.news is True
    assert report.signature == ("trade:11>22",)


def test_the_same_deal_worth_a_little_less_is_the_same_deal(monkeypatch):
    """It stands unchanged for days while nobody acts on it. Digesting the
    numbers would repost it every morning until somebody did."""
    _as_espn(monkeypatch)
    monkeypatch.setattr("combine.pipeline.trades.for_league",
                        lambda lg, **k: [_deal("11", "22", 32.0)])
    first = bot.build_trades("rcl").signature
    monkeypatch.setattr("combine.pipeline.trades.for_league",
                        lambda lg, **k: [_deal("11", "22", 29.0)])
    assert bot.build_trades("rcl").signature == first


def test_a_different_partner_is_a_new_report(monkeypatch):
    _as_espn(monkeypatch)
    monkeypatch.setattr("combine.pipeline.trades.for_league",
                        lambda lg, **k: [_deal("11", "33", 32.0)])
    assert bot.build_trades("rcl").signature == ("trade:11>33",)


def test_no_deal_is_not_news_and_never_posts(monkeypatch):
    """The usual answer in a balanced league. It belongs in /trades when asked
    and nowhere near the morning post."""
    _as_espn(monkeypatch)
    monkeypatch.setattr("combine.pipeline.trades.for_league",
                        lambda lg, **k: [])
    report = bot.build_trades("rcl")
    assert report.news is False
    assert report.signature == ()


def test_a_league_without_the_api_is_not_news_either(monkeypatch):
    monkeypatch.setattr(bot.config, "get_league",
                        lambda lg: type("C", (), {"name": "Work",
                                                  "platform": "yahoo"})())
    assert bot.build_trades("work").news is False


class Channel:
    """Just enough of a Discord channel to record what was posted."""

    def __init__(self):
        self.posts = []

    async def send(self, *, embeds=None, embed=None):
        self.posts.append(embeds if embeds is not None else [embed])


def _run_check(monkeypatch, *, startsit, wire, trades):
    """Drive one pass of the daily check with the three builders stubbed."""
    import asyncio

    channel = Channel()
    monkeypatch.setattr(bot.config, "leagues", lambda: {"rcl": object()})
    monkeypatch.setattr(bot, "already_said", lambda *a: False)
    monkeypatch.setattr(bot, "current_week", lambda: 3)
    monkeypatch.setattr(bot, "build_startsit", startsit)
    monkeypatch.setattr(bot, "build_waivers", wire)
    monkeypatch.setattr(bot, "build_trades", trades)
    client = type("B", (), {"get_channel": lambda self, cid: channel})()
    asyncio.run(bot.daily_check.coro(client))
    return channel


def test_a_trade_failure_does_not_swallow_the_lineup_report(monkeypatch):
    """Trades is the slowest thing in the check and the likeliest to fail. A
    lineup problem already in hand must still go out."""
    def boom(_slug):
        raise RuntimeError("espn fell over")

    channel = _run_check(
        monkeypatch,
        startsit=lambda slug, wk: bot.Report(["LINEUP"], True, ("hurt:1",)),
        wire=lambda slug, wk: bot.Report([]),
        trades=boom)
    assert [e for post in channel.posts for e in post] == ["LINEUP"]


def test_a_trade_on_its_own_is_enough_to_post(monkeypatch):
    """The lineup being right is the usual state. It must not keep a deal
    quiet, which is the whole reason this is in the morning post."""
    channel = _run_check(
        monkeypatch,
        startsit=lambda slug, wk: bot.Report([]),
        wire=lambda slug, wk: bot.Report([]),
        trades=lambda slug: bot.Report(["TRADE"], True, ("trade:11>22",)))
    assert [e for post in channel.posts for e in post] == ["TRADE"]


def test_nothing_to_say_stays_silent(monkeypatch):
    channel = _run_check(
        monkeypatch,
        startsit=lambda slug, wk: bot.Report([]),
        wire=lambda slug, wk: bot.Report([]),
        trades=lambda slug: bot.Report([]))
    assert channel.posts == []
