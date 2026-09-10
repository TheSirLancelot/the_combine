"""Discord bot: the in-season interface from anywhere.

Chosen over exposing the Streamlit app through a tunnel because of the direction
of the connection. This dials OUT to Discord's gateway and holds a websocket, so
there is no public hostname, no ingress rule, no Access policy to keep correct
and no inbound surface at all. It also collapses both wanted behaviours into one
process: slash commands for asking, and a scheduled check for being told.

READ ONLY against ESPN and Yahoo, and more emphatically than elsewhere in this
repo, because this is the one component that takes instructions from a chat
window. There are no write or transaction commands against a league and none
should ever be added.

`/clear` is the one command that writes anything anywhere, and it writes to
Discord: it deletes the bot's own noise out of its own channel. That is a
different thing from a roster move, but it is still destructive and
irreversible, so it dry runs by default and only deletes when told to.

Two mechanics that matter and are easy to get wrong:

  * Discord wants a response within three seconds, and this pipeline takes
    several to hit ESPN and PFF. So every command defers first and follows up
    when the work is done.
  * discord.py runs one event loop, and the pipeline is synchronous blocking
    I/O. Doing that work on the loop freezes the heartbeat and the gateway drops
    the connection, so it runs in a thread.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from functools import lru_cache
from typing import NamedTuple

import discord
from discord import app_commands
from discord.ext import tasks

from . import config

log = logging.getLogger("combine.bot")

# The scheduled check. Every morning Pacific, not just Sunday: games are played
# on Thursday, Saturday, Sunday and Monday, so a Sunday-only check misses a
# Thursday injury and every waiver window that opens midweek.
#
# Running daily only works because of the digest below. The gate that made a
# Sunday check bearable was "post only when there is news", and news does not
# stop being news the next morning: a hurt starter would have posted the same
# card six days running, which is how a channel gets muted.
CHECK_AT = dtime(hour=15, minute=30, tzinfo=UTC)   # 08:30 PT
STATE_PATH = config.DATA_DIR / "last_post.json"


def _int_env(key: str) -> int:
    raw = os.environ.get(key) or ""
    return int(raw) if raw.strip().isdigit() else 0


OWNER_ID = _int_env("DISCORD_OWNER_ID")
GUILD_ID = _int_env("DISCORD_GUILD_ID")
CHANNEL_ID = _int_env("DISCORD_CHANNEL_ID")

LEAGUE_CHOICES = [
    app_commands.Choice(name=f"{slug} · {cfg.name}", value=slug)
    for slug, cfg in config.leagues().items()
]


# --- the work, all of it synchronous and run off the event loop -------------

@lru_cache(maxsize=1)
def _distribution():
    """Outcome history, or None. Optional by design: a missing database costs
    the floor and ceiling columns, not the answer.

    Cached: it is identical for every league, and asking for all three at once
    would otherwise rebuild the same frame three times.
    """
    from . import db
    from .pipeline.distribution import load as load_dist

    try:
        with db.connect(readonly=True) as conn:
            dist = load_dist(conn, config.SEASON - 1)
        return None if dist.empty else dist
    except Exception as exc:
        log.warning("no outcome history: %s", exc)
        return None


@lru_cache(maxsize=1)
def _pff():
    """(usage, ids, in_season). Optional the same way."""
    from .pipeline.crosswalk import load_ids
    from .pipeline.providers.pff_api import PffApi
    from .pipeline.usage import load as load_usage

    try:
        api = PffApi()
        return load_usage(api), load_ids(), api.season_state().in_season
    except Exception as exc:
        log.warning("no pff usage: %s", exc)
        return {}, {}, True


class Report(NamedTuple):
    """What a builder hands back.

    `signature` is what the report is ABOUT -- which players, which swaps --
    with the numbers left out on purpose. Digesting the rendered text instead
    would defeat the whole thing, because ESPN nudges projections through the
    day and every morning would look like new news.
    """

    embeds: list[discord.Embed]
    news: bool = False
    signature: tuple[str, ...] = ()


def already_said(slug: str, week: int, signature: tuple[str, ...]) -> bool:
    """Whether this exact report already went out. Records it if not.

    Best effort: any failure to read or write the state file returns False, so
    the worst case is a duplicate post rather than a missed one. That is the
    right way round.
    """
    if not signature:
        return False
    digest = hashlib.sha1(
        "|".join(sorted(signature)).encode(), usedforsecurity=False).hexdigest()
    try:
        state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        log.warning("could not read %s; posting anyway", STATE_PATH, exc_info=True)
        state = {}
    seen = state.get(slug) or {}
    if seen.get("week") == week and seen.get("digest") == digest:
        return True
    state[slug] = {"week": week, "digest": digest,
                   "at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except Exception:
        log.warning("could not write %s", STATE_PATH, exc_info=True)
    return False


def _log_recommendations(league: str, week: int, rows) -> None:
    """Write down what we just advised, so it can be graded later.

    Deliberately not load-bearing. This is bookkeeping for a scorecard, and a
    locked database or a missing table must never be the reason a Sunday morning
    lineup check fails to arrive.
    """
    if not rows:
        return
    try:
        from .pipeline.scorecard import record

        record(league, config.SEASON, int(week), rows)
    except Exception:
        log.warning("could not record recommendations for %s", league,
                    exc_info=True)


def failure_embed(slug: str, exc: Exception) -> discord.Embed:
    """One league failing must not take the others with it.

    Expired ESPN cookies in one league should not hide the Yahoo league's
    answer, so a failure renders as a red card in the same reply rather than
    aborting the command.
    """
    from . import discord_out

    return discord_out.message_embed(
        f"`{type(exc).__name__}: {exc}`", title=f"⚠️ {slug} failed",
        colour=discord_out.BAD)


def build_week(league: str, week: int | None = None) -> list[discord.Embed]:
    from . import discord_out
    from .platforms import client_for

    client = client_for(league)
    matchup = client.matchup(week)
    stamp = datetime.now().astimezone().strftime("%H:%M %Z")
    return discord_out.week_embeds(matchup, client.roster_slots(),
                                   config.get_league(league).name, stamp)


def build_startsit_all(week: int | None = None) -> list[discord.Embed]:
    """Every configured league, one after another.

    A league that errors is reported inline rather than taking the others with
    it: expired ESPN cookies should not hide the Yahoo league's answer.
    """
    messages: list[discord.Embed] = []
    for slug in config.leagues():
        try:
            messages += build_startsit(slug, week).embeds
        except Exception as exc:
            log.exception("startsit failed for %s", slug)
            messages.append(failure_embed(slug, exc))
    return messages


def build_week_all(week: int | None = None) -> list[discord.Embed]:
    messages: list[discord.Embed] = []
    for slug in config.leagues():
        try:
            messages += build_week(slug, week)
        except Exception as exc:
            log.exception("week failed for %s", slug)
            messages.append(failure_embed(slug, exc))
    return messages


def build_startsit(league: str, week: int | None = None) -> Report:
    from . import discord_out
    from .pipeline import scorecard
    from .pipeline.lineup import optimal_moves
    from .pipeline.startsit import review
    from .platforms import client_for

    client = client_for(league)
    matchup = client.matchup(week)
    dist = _distribution()
    usage, ids, in_season = _pff()
    calls, hurt = review(matchup, usage, ids, dist=dist)
    slots = client.roster_slots()
    _add, _drop, gain = optimal_moves(matchup.my_lineup, slots)
    _log_recommendations(league, matchup.week,
                         scorecard.from_startsit(calls, _add, _drop))
    messages = discord_out.startsit_embeds(
        matchup, calls, hurt, usage, ids, in_season,
        config.get_league(league).name, slots, dist)
    signature = tuple(
        [f"hurt:{p.player_id}" for p in hurt]
        + [f"swap:{c.bench.player_id}>{c.starter.player_id}" for c in calls]
        + [f"optimal:{p.player_id}" for p in _add])
    return Report(messages, discord_out.has_news(calls, hurt, gain), signature)


def build_compare(league: str, a: str, b: str,
                  week: int | None = None) -> list[discord.Embed]:
    from .pipeline.startsit import head_to_head
    from .platforms import client_for

    client = client_for(league)
    matchup = client.matchup(week)
    pool = matchup.my_lineup + matchup.their_lineup

    def find(want: str):
        needle = want.strip().lower()
        hits = [p for p in pool if needle in p.name.lower()]
        if len(hits) == 1:
            return hits[0], ""
        if hits:
            return None, (f"`{want}` matches {len(hits)}: "
                          + ", ".join(p.name for p in hits))
        return None, (f"`{want}` is not in this week's matchup. compare works on "
                      f"rostered players.")

    first, err_a = find(a)
    second, err_b = find(b)
    if first is None or second is None:
        from . import discord_out

        return [discord_out.message_embed(
            " ".join(x for x in (err_a, err_b) if x), "No match")]
    usage, ids, in_season = _pff()
    from . import discord_out

    text = head_to_head(first, second, usage, ids, in_season, matchup.week)
    return discord_out.compare_embeds(
        text, f"{first.name} vs {second.name} · week {matchup.week}")


@lru_cache(maxsize=8)
def _calibration(league: str):
    from .pipeline.calibration import load as load_cal

    return load_cal(league)


def build_waivers(league: str, week: int | None = None) -> Report:
    from . import discord_out
    from .pipeline import scorecard
    from .pipeline.waivers import find, season_values
    from .platforms import client_for

    cfg = config.get_league(league)
    if cfg.platform != "espn":
        return Report(discord_out.waivers_embeds(
            [], cfg.name, int(week or 0),
            unavailable="no free agent pool without the Yahoo API"))

    client = client_for(league)
    wk = int(week or client.week)
    found = find(client, wk, cal=_calibration(league),
                 season_value=season_values(client), dist=_distribution())
    # Only an add that does not trade away season value is worth a notification.
    # The rest belong in `/waivers` when you go looking, not in a Sunday ping.
    _log_recommendations(league, wk, scorecard.from_waivers(found))
    worth_telling = any(not c.trades_down for c in found)
    return Report(discord_out.waivers_embeds(found, cfg.name, wk), worth_telling,
                  tuple(f"add:{c.name}>{c.drop_name}" for c in found))


def build_waivers_all(week: int | None = None) -> list[discord.Embed]:
    messages: list[discord.Embed] = []
    for slug in config.leagues():
        try:
            messages += build_waivers(slug, week).embeds
        except Exception as exc:
            log.exception("waivers failed for %s", slug)
            messages.append(failure_embed(slug, exc))
    return messages


def build_scoreboard(week: int | None = None) -> list[discord.Embed]:
    from . import discord_out
    from .pipeline.scoreboard import build

    games, missing = build(week)
    return discord_out.scoreboard_embeds(games, missing)


def build_health() -> list[discord.Embed]:
    from . import discord_out
    from .platforms import client_for

    rows = []
    for slug, cfg in config.leagues().items():
        try:
            rows.append((True, f"{slug} ({cfg.platform}) {client_for(slug).ping()}"))
        except Exception as exc:
            rows.append((False, f"{slug} ({cfg.platform}) "
                                f"{type(exc).__name__}: {exc}"))
    return discord_out.health_embeds(rows)


def build_glossary() -> list[discord.Embed]:
    from . import discord_out
    from .pipeline.usage import GLOSSARY, OUTCOME_GLOSSARY

    return discord_out.glossary_embeds(GLOSSARY, OUTCOME_GLOSSARY)


# --- the client -------------------------------------------------------------

class Combine(discord.Client):
    def __init__(self) -> None:
        # `guilds` only. It is NOT a privileged intent, and it is required:
        # without it there is no channel cache, so get_channel returns None and
        # the scheduled check silently posts nowhere. Intents.none() looked
        # tidier and was wrong, which a connection test caught and reading the
        # docs would not have.
        #
        # Message Content and the other privileged intents stay off. Slash
        # commands need none of them, and asking would widen what a leaked token
        # could do for no gain.
        super().__init__(intents=discord.Intents(guilds=True))
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        # Without this the buttons on every message already in the channel stop
        # working the moment this process restarts.
        self.add_dynamic_items(Nav)
        guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
        if guild:
            # Guild-scoped commands appear immediately; global ones can take an
            # hour to propagate, which makes iterating miserable.
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if CHANNEL_ID:
            daily_check.start(self)
            weekly_score.start(self)
            kickoff_check.start(self)


client = Combine()


def owner_only():
    """Commands answer to one person.

    Without this, anyone who can see the bot can read the rosters and, more to
    the point, cause ESPN requests authenticated as the owner.
    """
    async def predicate(interaction: discord.Interaction) -> bool:
        if OWNER_ID and interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This bot answers to its owner only.", ephemeral=True)
            return False
        return True

    return app_commands.check(predicate)


BUILDERS = {
    "week": lambda league, week: (build_week(league, week) if league
                                  else build_week_all(week)),
    "startsit": lambda league, week: (build_startsit(league, week).embeds if league
                                      else build_startsit_all(week)),
    "waivers": lambda league, week: (build_waivers(league, week).embeds if league
                                     else build_waivers_all(week)),
    "scoreboard": lambda league, week: build_scoreboard(week),
}


@lru_cache(maxsize=1)
def _week_for(_day: str) -> int:
    """The current NFL week, resolved once a day.

    The nav buttons need a concrete number to step from, and "current" is not
    one. Cached on the date because it costs a league fetch and cannot change
    within a day.
    """
    from .platforms import client_for

    for slug in config.leagues():
        try:
            return int(client_for(slug).week)
        except Exception:
            log.debug("could not read the week from %s", slug, exc_info=True)
    return 1


def current_week() -> int:
    return _week_for(datetime.now().astimezone().strftime("%Y-%m-%d"))


class Nav(discord.ui.DynamicItem[discord.ui.Button],
          template=r"cmb:(?P<kind>[a-z]+):(?P<league>[a-z-]*):(?P<week>\d+)"):
    """Refresh and week-stepping buttons under a reply.

    A DynamicItem rather than a plain View on purpose. A normal view lives in
    the process that sent it, so every button in the channel goes dead the
    moment the bot restarts, and a button that silently does nothing is worse
    than no button. This carries its whole state in the custom_id, so the
    handler is reconstructed from the click and buttons keep working across
    restarts and redeploys forever.
    """

    def __init__(self, kind: str, league: str, week: int, label: str,
                 style=discord.ButtonStyle.secondary, emoji: str | None = None):
        super().__init__(discord.ui.Button(
            label=label, style=style, emoji=emoji,
            custom_id=f"cmb:{kind}:{league or '-'}:{max(week, 1)}"))
        self.kind, self.league, self.week = kind, league, week

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        league = match["league"]
        return cls(match["kind"], "" if league == "-" else league,
                   int(match["week"]), "")

    async def callback(self, interaction: discord.Interaction):
        # The owner check has to be repeated here. Anyone who can see the
        # message can click the button, and the check on the slash command does
        # not carry over to a component interaction.
        if OWNER_ID and interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This bot answers to its owner only.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            embeds = await asyncio.to_thread(
                BUILDERS[self.kind], self.league or None, self.week)
        except Exception as exc:
            log.exception("nav %s failed", self.kind)
            embeds = [failure_embed(self.league or "all leagues", exc)]
        await interaction.edit_original_response(
            embeds=embeds[:10], view=nav_row(self.kind, self.league, self.week))


def nav_row(kind: str, league: str | None, week: int | None) -> discord.ui.View:
    """Refresh, and a step either side of the week being shown."""
    here = int(week or current_week())
    view = discord.ui.View(timeout=None)
    if here > 1:
        view.add_item(Nav(kind, league or "", here - 1, f"Week {here - 1}",
                          emoji="◀"))
    view.add_item(Nav(kind, league or "", here, "Refresh",
                      style=discord.ButtonStyle.primary, emoji="🔄"))
    view.add_item(Nav(kind, league or "", here + 1, f"Week {here + 1}", emoji="▶"))
    return view


async def respond(interaction: discord.Interaction, work, *args, nav=None):
    """Defer, do the blocking work in a thread, then follow up.

    Both halves matter: the three second deadline, and keeping synchronous I/O
    off the event loop so the gateway heartbeat survives.

    `nav` is (kind, league, week) for the commands that can be refreshed or
    stepped a week either way. Everything else gets no buttons, because a
    glossary does not have a next page.
    """
    from . import discord_out

    await interaction.response.defer(thinking=True)
    try:
        embeds = await asyncio.to_thread(work, *args)
    except Exception as exc:
        log.exception("command failed")
        await interaction.followup.send(embed=discord_out.message_embed(
            f"`{type(exc).__name__}: {exc}`\n\nIf this is a 401 or an empty "
            f"league, the ESPN cookies expired: run "
            f"`python scripts/refresh_espn_cookies.py`.",
            title="Command failed", colour=discord_out.BAD))
        return
    if isinstance(embeds, tuple):
        embeds = embeds[0]
    if not embeds:
        embeds = [discord_out.message_embed("Nothing to report.", colour=discord_out.GOOD)]

    # Discord takes ten embeds per message. The buttons go on the last one, so
    # they sit at the bottom of the reply where a thumb lands.
    batches = [embeds[i:i + 10] for i in range(0, len(embeds), 10)]
    for i, batch in enumerate(batches):
        last = i == len(batches) - 1
        view = nav_row(*nav) if (nav and last) else discord.utils.MISSING
        await interaction.followup.send(embeds=batch, view=view)


@client.tree.command(description="This week's lineup, with projections and opponents")
@app_commands.describe(league="Which league, blank for all of them",
                       week="Week number, blank for current")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def week(interaction: discord.Interaction, league: str | None = None,
               week: int | None = None):
    await respond(interaction,
                  build_week_all if league is None else build_week,
                  *( (week,) if league is None else (league, week) ),
                  nav=("week", league, week))


@client.tree.command(description="Start/sit calls: only the slots with a real question")
@app_commands.describe(league="Which league, blank for all of them",
                       week="Week number, blank for current")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def startsit(interaction: discord.Interaction, league: str | None = None,
                   week: int | None = None):
    await respond(interaction,
                  build_startsit_all if league is None
                  else (lambda lg, wk: build_startsit(lg, wk).embeds),
                  *( (week,) if league is None else (league, week) ),
                  nav=("startsit", league, week))


@client.tree.command(description="Two players head to head, in one league")
@app_commands.describe(league="Which league", player_a="First player",
                       player_b="Second player")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def compare(interaction: discord.Interaction, league: str,
                  player_a: str, player_b: str):
    await respond(interaction, build_compare, league, player_a, player_b)


@client.tree.command(description="Free agents who would improve your lineup")
@app_commands.describe(league="Which league, blank for all of them",
                       week="Week number, blank for current")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def waivers(interaction: discord.Interaction, league: str | None = None,
                  week: int | None = None):
    await respond(interaction,
                  build_waivers_all if league is None
                  else (lambda lg, wk: build_waivers(lg, wk).embeds),
                  *( (week,) if league is None else (league, week) ),
                  nav=("waivers", league, week))


@client.tree.command(description="Live scores across every league")
@app_commands.describe(week="Week number, blank for current")
@owner_only()
async def scoreboard(interaction: discord.Interaction, week: int | None = None):
    await respond(interaction, build_scoreboard, week,
                  nav=("scoreboard", None, week))


@client.tree.command(description="What the Role and outcome numbers mean")
@owner_only()
async def glossary(interaction: discord.Interaction):
    await respond(interaction, build_glossary)


@client.tree.command(description="How the tool's own recommendations have done")
@app_commands.describe(week="Grade this week now instead of waiting for Tuesday")
@owner_only()
async def scorecard(interaction: discord.Interaction, week: int | None = None):
    if week is not None:
        await respond(interaction,
                      lambda wk: build_scorecard(score_last_week(wk)[0]), week)
    else:
        await respond(interaction, build_scorecard, None)


@client.tree.command(description="Per-league connection status")
@owner_only()
async def health(interaction: discord.Interaction):
    await respond(interaction, build_health)


# Discord bulk-deletes in one request, but only messages under 14 days old.
# Older ones go one at a time and get rate limited to roughly one a second, so a
# long-lived channel is minutes of work rather than an instant wipe. Worth
# saying in the dry run so the wait is expected rather than alarming.
BULK_WINDOW = timedelta(days=14)
COUNT_CAP = 2000        # how far back a dry run bothers to count


@client.tree.command(
    description="Delete messages in this channel. Irreversible. Dry runs by default.")
@app_commands.describe(
    limit="How many messages back. Blank means the whole channel.",
    confirm="Must be True to actually delete. Left off, this only reports.")
@owner_only()
async def clear(interaction: discord.Interaction, limit: int | None = None,
                confirm: bool = False):
    """Housekeeping for the bot's own channel.

    Not routed through `respond`: purge is async I/O against Discord rather than
    blocking work against ESPN, so it belongs on the loop, and the reply is
    ephemeral so the progress message cannot be caught in its own purge.
    """
    channel = interaction.channel
    # Messageable is not enough: DMs can be read but not purged, so the check
    # has to be for the channel types that actually have purge().
    if not isinstance(channel, discord.TextChannel | discord.Thread):
        await interaction.response.send_message(
            "This only works in a server text channel.", ephemeral=True)
        return

    from . import discord_out

    await interaction.response.defer(ephemeral=True, thinking=True)
    cutoff = datetime.now(UTC) - BULK_WINDOW
    where = f"#{getattr(channel, 'name', 'this channel')}"

    try:
        if not confirm:
            recent = old = 0
            async for message in channel.history(limit=limit or COUNT_CAP):
                if message.created_at >= cutoff:
                    recent += 1
                else:
                    old += 1
            total = recent + old
            if not total:
                await interaction.followup.send(
                    embed=discord_out.message_embed(
                        f"{where} is already empty.", colour=discord_out.GOOD),
                    ephemeral=True)
                return
            capped = "+" if limit is None and total >= COUNT_CAP else ""
            note = ""
            if old:
                note = (f"\n{old} of them are over 14 days old and have to go one "
                        f"at a time, so expect roughly {old // 60 + 1} minute(s) "
                        f"of deleting.")
            await interaction.followup.send(
                embed=discord_out.message_embed(
                    f"Would delete **{total}{capped}** message(s) from {where}. "
                    f"This cannot be undone.{note}\n\nRun it again with "
                    f"`confirm: True` to go ahead.",
                    title="Dry run", colour=discord_out.WARN),
                ephemeral=True)
            return

        started = datetime.now(UTC)
        deleted = await channel.purge(
            limit=limit, reason=f"/clear by {interaction.user}")
        elapsed = (datetime.now(UTC) - started).total_seconds()
        log.info("cleared %d message(s) from %s in %.0fs",
                 len(deleted), where, elapsed)
        try:
            await interaction.followup.send(
                embed=discord_out.message_embed(
                    f"Deleted **{len(deleted)}** message(s) from {where} in "
                    f"{elapsed:.0f}s.", title="Cleared",
                    colour=discord_out.GOOD),
                ephemeral=True)
        except discord.HTTPException:
            # A long purge can outlive the 15 minute interaction token. The
            # deleting already happened; only the receipt is lost.
            log.warning("purge finished after the interaction expired")

    except discord.Forbidden:
        await interaction.followup.send(
            "I need **Manage Messages** and **Read Message History** on this "
            "channel. Right-click the channel, Edit Channel, Permissions, then "
            "add this bot. The developer portal's permission checkboxes only "
            "build the invite link and do not change a bot that is already in "
            "the server.", ephemeral=True)
    except discord.HTTPException as exc:
        log.exception("clear failed")
        await interaction.followup.send(f"`{type(exc).__name__}: {exc}`",
                                        ephemeral=True)


async def send_embeds(channel, embeds: list[discord.Embed]) -> int:
    """Discord takes ten embeds per message. Returns how many messages went."""
    sent = 0
    for i in range(0, len(embeds), 10):
        await channel.send(embeds=embeds[i:i + 10])
        sent += 1
    return sent


@tasks.loop(time=CHECK_AT)
async def daily_check(bot: discord.Client):
    """Post only when there is something NEW to say.

    Silence is the feature, and running daily raises the bar for what silence
    means. A correct lineup produces nothing, and so does a report that already
    went out: a starter who is out for the season is news once, not every
    morning until Sunday.
    """
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        # Cache miss, or a channel created since connect. REST always knows.
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except Exception as exc:
            log.warning("channel %s unreachable: %s", CHANNEL_ID, exc)
            return
    for slug in config.leagues():
        try:
            startsit = await asyncio.to_thread(build_startsit, slug, None)
            # A waiver upgrade that does not cost season value is the strongest
            # validated signal here: +2.50 points a week in RCL over 216
            # team-weeks. It belongs in the morning post.
            wire = await asyncio.to_thread(build_waivers, slug, None)
        except Exception as exc:
            log.exception("weekly check failed for %s", slug)
            await channel.send(embed=failure_embed(f"{slug} check", exc))
            continue
        if not (startsit.news or wire.news):
            log.info("%s: nothing worth posting", slug)
            continue
        week = current_week()
        parts, signature = [], ()
        if startsit.news:
            parts += startsit.embeds
            signature += startsit.signature
        if wire.news:
            parts += wire.embeds
            signature += wire.signature
        if already_said(slug, week, signature):
            log.info("%s: same report as last time, staying quiet", slug)
            continue
        await send_embeds(channel, parts)


# The last chance to act. Once a game kicks off the lineup is locked, so a
# starter ruled out at 10am on Sunday is a zero you can still avoid at 11:00 and
# cannot avoid at 13:01. The morning check runs at 08:30 and cannot see a
# downgrade that lands after it.
KICKOFF_WARNING = timedelta(minutes=90)
KICKOFF_POLL = 15          # minutes


def kickoff_problems(league: str, now_ms: int | None = None):
    """Starters who cannot play and whose game is about to lock. (week, players).

    Cheap first: the pro schedule is cached per week, so a poll on a Wednesday
    afternoon costs nothing and returns before touching a box score. Only a
    kickoff inside the window is worth paying for a lineup.
    """
    import time

    from .pipeline.lineup import BAD_STATUS, split
    from .platforms import client_for

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    horizon = now_ms + int(KICKOFF_WARNING.total_seconds() * 1000)

    client = client_for(league)
    week = int(client.week)
    schedule = client.pro_schedule(week)
    imminent = {team for team, game in schedule.items()
                if now_ms < game.kickoff_ms <= horizon}
    if not imminent:
        return week, []

    starters, _bench = split(client.matchup(week).my_lineup)
    return week, [p for p in starters
                  if (p.team in imminent)
                  and (p.on_bye or p.status in BAD_STATUS)]


def build_kickoff_alert(league: str, week: int, players) -> list[discord.Embed]:
    from . import discord_out

    cfg = config.get_league(league)
    e = discord.Embed(
        title=f"🚨 Kicking off soon · {cfg.name}", colour=discord_out.BAD,
        description=("These starters cannot play and their game locks within "
                     "the hour and a half. After kickoff you are stuck with "
                     "them."))
    for p in players:
        discord_out.field(
            e, f"{p.slot} · {p.name}",
            f"{'on bye' if p.on_bye else p.status} · {p.opponent} · "
            f"projected {p.projected:.1f}")
    e.set_footer(text=f"week {week}")
    return [e]


@tasks.loop(minutes=KICKOFF_POLL)
async def kickoff_check(bot: discord.Client):
    """The one alert worth interrupting for, because it expires.

    Everything else this bot says keeps until you look. This does not: the
    window closes at kickoff and the cost of missing it is a zero in a starting
    slot.
    """
    if not CHANNEL_ID:
        return
    for slug, cfg in config.leagues().items():
        if cfg.platform != "espn":
            continue           # no kickoff times without the API
        try:
            week, players = await asyncio.to_thread(kickoff_problems, slug)
        except Exception:
            log.exception("kickoff check failed for %s", slug)
            continue
        if not players:
            continue
        # Once per player per week. The poll runs every 15 minutes and the
        # window is 90, so without this one ruled-out starter is six pings.
        signature = tuple(f"kickoff:{p.player_id}:{p.status}" for p in players)
        if already_said(f"kickoff:{slug}", week, signature):
            continue
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            try:
                channel = await bot.fetch_channel(CHANNEL_ID)
            except Exception:
                log.warning("channel %s unreachable for a kickoff alert", CHANNEL_ID)
                return
        await send_embeds(channel, build_kickoff_alert(slug, week, players))


# Tuesday morning Pacific, after Monday night is final and scored.
SCORE_AT = dtime(hour=16, minute=0, tzinfo=UTC)     # 09:00 PT
SCORE_DAY = 2                                        # Tuesday, as isoweekday


def score_last_week(week: int | None = None) -> tuple[int, int, int]:
    """Pull the finished week's actuals, then grade what we recommended.

    Returns (week scored, recommendations scored, still unresolved).
    """
    from . import db
    from .pipeline.history import pull_espn
    from .pipeline.scorecard import score

    target = int(week if week is not None else max(current_week() - 1, 1))
    done = missing = 0
    with db.connect() as conn:
        for slug, cfg in config.leagues().items():
            if cfg.platform != "espn":
                continue
            try:
                pull_espn(conn, slug, config.SEASON, weeks=[target],
                          log=lambda m: log.info("%s", m), refresh=True)
            except Exception:
                log.exception("could not pull week %s for %s", target, slug)
        got, left = score(conn, config.SEASON, target)
        done += got
        missing += left
    log.info("week %s: scored %d recommendation(s), %d unresolved",
             target, done, missing)
    return target, done, missing


@tasks.loop(time=SCORE_AT)
async def weekly_score(bot: discord.Client):
    """Grade last week once its games are final.

    Tuesday rather than Monday: Monday night finishes late enough that a Monday
    morning pass would score a week that is still being played, and a wrong
    grade is worse than a late one.
    """
    if datetime.now(UTC).isoweekday() != SCORE_DAY:
        return
    try:
        week, done, _unresolved = await asyncio.to_thread(score_last_week)
    except Exception:
        log.exception("weekly scoring failed")
        return
    if not done:
        log.info("nothing to score")
        return
    if not CHANNEL_ID:
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except Exception:
            log.warning("channel %s unreachable for the scorecard", CHANNEL_ID)
            return
    await send_embeds(channel, await asyncio.to_thread(build_scorecard, week))


def build_scorecard(week: int | None = None) -> list[discord.Embed]:
    from . import db, discord_out
    from .pipeline.scorecard import frame, summary

    with db.connect(readonly=True) as conn:
        df = frame(conn, config.SEASON)
    rows = summary(df)
    if not rows:
        return [discord_out.message_embed(
            "Nothing scored yet. Recommendations are written down as they are "
            "made and graded once the week's games are final.",
            title="Scorecard", colour=discord_out.DEAD)]

    overall = rows[-1]
    colour = (discord_out.GOOD if overall["points"] > 0
              else discord_out.BAD if overall["points"] < 0 else discord_out.INFO)
    title = "Scorecard" + (f" · through week {week}" if week else "")
    table = [f"{'KIND':<9}{'N':>4}{'RIGHT':>7}{'POINTS':>9}{'PER':>7}"]
    for r in rows:
        table.append(f"{r['kind']:<9}{r['n']:>4}{r['right'] * 100:>6.0f}%"
                     f"{r['points']:>+9.1f}{r['per_call']:>+7.2f}")
    e = discord.Embed(title=title, colour=colour,
                      description=discord_out.code("\n".join(table)))
    e.set_footer(text="RIGHT is how often the recommended player outscored the "
                      "one he would have replaced. This grades the tool, not "
                      "the manager: it counts what was recommended whether or "
                      "not it was acted on.")
    return [e]


def preflight() -> list[str]:
    """Everything checkable without touching the network. Returns complaints."""
    problems = []
    if not os.environ.get("DISCORD_TOKEN"):
        problems.append("DISCORD_TOKEN is unset. it belongs in .env and nowhere else.")
    if not OWNER_ID:
        problems.append("DISCORD_OWNER_ID is unset. refusing to run a bot that "
                        "answers to anyone.")
    if not GUILD_ID:
        problems.append("DISCORD_GUILD_ID is unset. commands would register "
                        "globally and take an hour to appear.")
    if not CHANNEL_ID:
        problems.append("DISCORD_CHANNEL_ID is unset. the scheduled check has "
                        "nowhere to post; slash commands would still work.")
    if not config.leagues():
        problems.append("no leagues configured. check .env against .env.example.")
    return problems


async def _post(messages: list[discord.Embed], channel_id: int) -> None:
    """Send without joining the gateway.

    `login` does the REST handshake only, and `fetch_channel` and `send` are
    plain HTTP, so this never opens a websocket. That matters because the
    long-running agent already holds one: a second gateway session would be a
    second copy of the bot answering every slash command twice.
    """
    # Same intents as the agent even though nothing connects: with none(),
    # discord.py warns "Guilds intent seems to be disabled" on every send, which
    # is noise on a path that never opens a gateway.
    poster = discord.Client(intents=discord.Intents(guilds=True))
    await poster.login(os.environ["DISCORD_TOKEN"])
    try:
        channel = await poster.fetch_channel(channel_id)
        await send_embeds(channel, messages)
    finally:
        await poster.close()


def notify(leagues: list[str] | None = None, force: bool = False,
           dry_run: bool = False) -> int:
    """Run the scheduled check now, on demand.

    Exists because the success condition of the weekly check is silence, which
    is indistinguishable from the whole thing being broken. `--force` posts even
    when there is nothing to report, which is the only way to prove delivery
    works on a quiet week.
    """
    wanted = leagues or list(config.leagues())
    posted = quiet = failed = 0

    for slug in wanted:
        try:
            messages, newsworthy, _signature = build_startsit(slug, None)
        except Exception as exc:
            log.error("%s: %s: %s", slug, type(exc).__name__, exc)
            failed += 1
            continue

        if not newsworthy and not force:
            print(f"{slug}: nothing worth posting (this is the normal case)")
            quiet += 1
            continue

        if force and not newsworthy:
            from . import discord_out

            messages = [discord_out.message_embed(
                "Nothing is actually wrong. The real check would have stayed "
                "silent; this is here to prove delivery works.",
                title="🧪 Manual test", colour=discord_out.INFO)] + messages

        if dry_run:
            from . import discord_out

            print(f"\n===== {slug}: would post {len(messages)} embed(s) =====")
            for message in messages:
                print(discord_out.embed_text(message))
                print()
            posted += 1
            continue

        if not CHANNEL_ID:
            print(f"{slug}: DISCORD_CHANNEL_ID is unset, nowhere to post")
            failed += 1
            continue

        asyncio.run(_post(messages, CHANNEL_ID))
        print(f"{slug}: posted {len(messages)} embed(s)")
        posted += 1

    verb = "would post" if dry_run else "posted"
    print(f"\n{verb} for {posted} league(s), {quiet} quiet, {failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> None:
    """Run the bot, or with --check just validate and exit.

    The first thing this does is log, before anything can fail. An empty log file
    used to be ambiguous between "never started" and "started and said nothing",
    and that ambiguity cost a debugging round: launchd had created the log files,
    so the job had clearly run, but there was nothing in them to say why it
    stopped. A banner on line one makes the next failure readable.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        # force, because something upstream may already have configured the root
        # logger, in which case basicConfig is silently a no-op.
        force=True,
    )
    log.info("combine bot starting: python %s, cwd %s",
             sys.version.split()[0], os.getcwd())
    log.info("leagues: %s", ", ".join(config.leagues()) or "NONE")

    problems = preflight()
    for complaint in problems:
        log.error("%s", complaint)
    if any("refusing" in c or "unset. it belongs" in c for c in problems):
        raise SystemExit(1)

    if argv and "--check" in argv:
        log.info("--check: configuration is usable, not connecting")
        return

    client.run(os.environ["DISCORD_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main(sys.argv[1:])
