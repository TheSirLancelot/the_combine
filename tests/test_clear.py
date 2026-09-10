"""`/clear` is the only command in this repo that destroys anything.

The tests that matter are the two guards: it must not delete without an
explicit confirm, and a missing permission must produce the fix rather than a
stack trace.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import discord
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import bot


class FakeMessage:
    def __init__(self, age_days: float):
        self.created_at = datetime.now(UTC) - timedelta(days=age_days)


class FakeChannel:
    """Stands in for a TextChannel. isinstance is patched around it."""

    name = "bot-updates"

    def __init__(self, messages, forbidden=False):
        self.messages = messages
        self.forbidden = forbidden
        self.purged = None

    def history(self, limit=None):
        messages = self.messages if limit is None else self.messages[:limit]

        async def gen():
            for m in messages:
                yield m

        return gen()

    async def purge(self, limit=None, reason=None):
        if self.forbidden:
            raise discord.Forbidden(_Response(), "missing permissions")
        self.purged = (limit, reason)
        return self.messages if limit is None else self.messages[:limit]


class _Response:
    status = 403
    reason = "Forbidden"


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, embed=None, ephemeral=False):
        from combine.discord_out import embed_text

        self.sent.append(content if embed is None else embed_text(embed))


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.sent = []

    async def defer(self, ephemeral=False, thinking=False):
        self.deferred = True

    async def send_message(self, content, ephemeral=False):
        self.sent.append(content)


class FakeInteraction:
    def __init__(self, channel):
        self.channel = channel
        self.user = "william"
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def any_channel(monkeypatch):
    """`clear` type-checks for TextChannel | Thread; the fake is neither."""
    real = bot.discord.TextChannel
    monkeypatch.setattr(bot.discord, "TextChannel", FakeChannel)
    yield
    bot.discord.TextChannel = real


def run(interaction, **kwargs):
    asyncio.run(bot.clear.callback(interaction, **kwargs))


def test_a_bare_clear_deletes_nothing(any_channel):
    """The whole safety story. Without confirm it is a report, not an action."""
    channel = FakeChannel([FakeMessage(1) for _ in range(5)])
    interaction = FakeInteraction(channel)
    run(interaction)
    assert channel.purged is None
    said = interaction.followup.sent[0]
    assert "Would delete" in said and "5" in said
    assert "confirm: True" in said


def test_confirm_actually_purges(any_channel):
    channel = FakeChannel([FakeMessage(1) for _ in range(3)])
    interaction = FakeInteraction(channel)
    run(interaction, confirm=True)
    assert channel.purged == (None, "/clear by william")
    assert "Deleted **3**" in interaction.followup.sent[0]


def test_the_dry_run_warns_about_the_slow_path(any_channel):
    """Messages over 14 days delete one at a time. A wipe that takes minutes
    with no warning reads as a hung bot."""
    channel = FakeChannel([FakeMessage(30) for _ in range(120)]
                          + [FakeMessage(1) for _ in range(4)])
    interaction = FakeInteraction(channel)
    run(interaction)
    said = interaction.followup.sent[0]
    assert "120 of them are over 14 days old" in said
    assert "minute" in said


def test_a_missing_permission_explains_the_fix(any_channel):
    channel = FakeChannel([FakeMessage(1)], forbidden=True)
    interaction = FakeInteraction(channel)
    run(interaction, confirm=True)
    said = interaction.followup.sent[0]
    assert "Manage Messages" in said and "Read Message History" in said
    assert "developer portal" in said


def test_limit_is_passed_through(any_channel):
    channel = FakeChannel([FakeMessage(1) for _ in range(10)])
    interaction = FakeInteraction(channel)
    run(interaction, limit=4, confirm=True)
    assert channel.purged == (4, "/clear by william")


def test_an_empty_channel_says_so(any_channel):
    channel = FakeChannel([])
    interaction = FakeInteraction(channel)
    run(interaction)
    assert "already empty" in interaction.followup.sent[0]
    assert channel.purged is None


def test_it_refuses_outside_a_server_channel():
    interaction = FakeInteraction(object())
    run(interaction)
    assert "server text channel" in interaction.response.sent[0]
