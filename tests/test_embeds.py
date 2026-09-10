"""Embeds and the buttons under them.

Two classes of failure worth catching here. An embed that breaches one of
Discord's caps is rejected whole, so the message never arrives and the only
symptom is silence, which is also what a quiet week looks like. And a button
carries its state in its custom_id, which has its own cap and has to survive a
round trip through a regex.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import discord
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import bot, discord_out
from combine.discord_out import DESC, FIELD, FIELDS, TOTAL, field


def test_field_refuses_rather_than_letting_discord_reject_the_message():
    """The 6000 character whole-embed cap is the dangerous one: it is invisible
    until a long week trips it, and it costs the entire message."""
    e = discord.Embed(title="t")
    added = sum(field(e, f"field {i}", "x" * 900) for i in range(30))
    assert added < 30                      # it stopped rather than overflowing
    assert len(e) <= 6000
    assert len(e.fields) <= FIELDS


def test_field_truncates_an_oversized_value_visibly():
    e = discord.Embed(title="t")
    assert field(e, "name", "y" * 5000) is True
    assert len(e.fields[0].value) <= FIELD
    assert e.fields[0].value.endswith("truncated")


def test_field_reports_whether_it_went_in():
    """The return value is what lets a caller say "notes shown for 3 of 8"
    instead of dropping the rest silently."""
    e = discord.Embed(title="t")
    assert field(e, "a", "short") is True
    e.description = "z" * DESC
    while field(e, "b", "x" * 900):
        pass
    assert field(e, "b", "x" * 900) is False
    assert len(e) <= 6000


def test_the_budget_leaves_room_for_a_footer():
    """Footers are set after the fields, so the budget has to reserve for one."""
    assert TOTAL + 2048 <= 6000 + 1548     # i.e. a normal footer still fits
    e = discord.Embed(title="t")
    while field(e, "f", "x" * 200):
        pass
    e.set_footer(text="x" * 400)
    assert len(e) <= 6000


# --- the nav buttons --------------------------------------------------------

def test_custom_id_round_trips():
    """A button's whole state lives in its custom_id. If the template does not
    match what __init__ writes, every button is silently dead."""
    item = bot.Nav("startsit", "rcl", 7, "Refresh")
    custom_id = item.item.custom_id
    assert len(custom_id) <= 100
    match = re.fullmatch(bot.Nav.__discord_ui_compiled_template__, custom_id)
    assert match, custom_id
    assert match["kind"] == "startsit"
    assert match["league"] == "rcl"
    assert int(match["week"]) == 7


def test_all_leagues_round_trips_through_a_sentinel():
    """An empty league would leave `cmb:week::3`, which the template still
    matches but reads as ambiguous; `-` is explicit."""
    item = bot.Nav("week", "", 3, "Refresh")
    assert ":-:" in item.item.custom_id
    match = re.fullmatch(bot.Nav.__discord_ui_compiled_template__,
                         item.item.custom_id)
    rebuilt = asyncio.run(bot.Nav.from_custom_id(None, None, match))
    assert rebuilt.league == ""
    assert rebuilt.kind == "week"
    assert rebuilt.week == 3


@pytest.mark.parametrize("kind", sorted(bot.BUILDERS))
def test_every_button_kind_has_a_builder(kind):
    """The callback looks the kind up in BUILDERS. A kind with no entry is a
    KeyError on click."""
    assert callable(bot.BUILDERS[kind])


def test_nav_row_offers_a_step_either_way(monkeypatch):
    monkeypatch.setattr(bot, "current_week", lambda: 5)
    ids = [i.custom_id for i in bot.nav_row("week", "rcl", None).children]
    assert [i.split(":")[-1] for i in ids] == ["4", "5", "6"]


def test_week_one_has_no_previous_week(monkeypatch):
    """Week 0 does not exist, and a button that errors is worse than no button."""
    monkeypatch.setattr(bot, "current_week", lambda: 1)
    row = bot.nav_row("week", "rcl", None)
    assert [i.custom_id.split(":")[-1] for i in row.children] == ["1", "2"]


def test_the_buttons_are_persistent():
    """timeout=None plus a DynamicItem is what keeps a button alive across a
    restart. A view with a timeout goes dead and clicking it does nothing."""
    assert bot.nav_row("week", "rcl", 3).timeout is None


# --- severity ---------------------------------------------------------------

def test_colour_is_severity_not_decoration():
    """Green means nothing to do, red means someone who cannot play is starting.
    It is the part you can read from a notification without opening anything."""
    assert discord_out.GOOD != discord_out.BAD
    for name in ("GOOD", "INFO", "WARN", "BAD", "DEAD"):
        assert 0 <= getattr(discord_out, name) <= 0xFFFFFF


def test_an_unavailable_league_is_grey_not_red():
    """The Yahoo league having no waiver pool is a fact, not a failure. Red
    would train him to ignore red."""
    out = discord_out.waivers_embeds([], "Degenerates", 3, unavailable="no API")
    assert out[0].colour.value == discord_out.DEAD


def test_a_failure_is_red_and_names_the_league():
    e = bot.failure_embed("rcl", RuntimeError("401 unauthorized"))
    assert e.colour.value == discord_out.BAD
    assert "rcl" in e.title
    assert "401 unauthorized" in e.description
