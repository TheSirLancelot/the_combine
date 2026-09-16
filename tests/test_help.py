"""The command list, and the guard that keeps it honest.

The valuable test in here is the drift one. A help page is only worth having
while it matches the commands that exist, and the usual way that stops being
true is somebody adding a command and forgetting this file. So the catalogue
and the command tree have to agree, or the suite fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine import bot, discord_out, helptext


def registered() -> set[str]:
    return {c.name for c in bot.client.tree.get_commands()}


def test_every_command_is_in_the_catalogue():
    missing = registered() - set(helptext.names())
    assert not missing, f"commands with no help entry: {sorted(missing)}"


def test_the_catalogue_has_no_commands_that_do_not_exist():
    stale = set(helptext.names()) - registered()
    assert not stale, f"help entries for commands that are gone: {sorted(stale)}"


def test_every_topic_lands_in_a_group_that_is_rendered():
    for topic in helptext.CATALOGUE:
        assert topic.group in helptext.GROUPS, topic.name


def test_every_group_has_something_in_it():
    for group in helptext.GROUPS:
        assert helptext.in_group(group), group


def test_find_takes_a_name_with_or_without_the_slash():
    assert helptext.find("waivers").name == "waivers"
    assert helptext.find("/waivers").name == "waivers"
    assert helptext.find("/WAIVERS").name == "waivers"
    assert helptext.find("nonsense") is None
    assert helptext.find("") is None


def test_the_overview_fits_inside_discord_s_caps():
    """A too-large embed is rejected outright and the whole message vanishes,
    which is invisible until the list grows."""
    e = discord_out.help_embeds()[0]
    assert len(e) <= 6000
    assert len(e.fields) <= discord_out.FIELDS
    for f in e.fields:
        assert len(f.value) <= 1024, f.name


def test_every_detail_page_fits_too():
    for topic in helptext.CATALOGUE:
        e = discord_out.help_detail(topic)[0]
        assert len(e) <= 6000, topic.name
        assert len(e.description) <= discord_out.DESC, topic.name


def test_a_command_with_a_known_id_renders_as_a_clickable_mention():
    e = discord_out.help_embeds({"week": "</week:123>"})[0]
    body = "\n".join(f.value for f in e.fields)
    assert "</week:123>" in body


def test_a_command_with_no_id_falls_back_to_plain_text():
    """Before the first sync there are no ids. Printing the raw mention syntax
    would render as literal angle brackets and look broken."""
    e = discord_out.help_embeds({})[0]
    body = "\n".join(f.value for f in e.fields)
    assert "/week" in body
    assert "</week:" not in body


def test_the_menu_offers_every_command_plus_a_way_back():
    options = bot.HelpPick().item.options
    assert options[0].value == helptext.OVERVIEW
    assert {o.value for o in options} == {helptext.OVERVIEW,
                                          *helptext.names()}
    assert len(options) <= 25, "Discord refuses a select with more than 25"


def test_the_menu_descriptions_fit():
    for option in bot.HelpPick().item.options:
        assert len(option.description or "") <= 100, option.value


def test_every_entry_says_more_than_its_one_liner():
    """The point of the detail view is the part the slash command description
    cannot carry."""
    for topic in helptext.CATALOGUE:
        assert len(topic.detail) > len(topic.line), topic.name
