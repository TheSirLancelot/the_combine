"""Discord bot: the in-season interface from anywhere.

Chosen over exposing the Streamlit app through a tunnel because of the direction
of the connection. This dials OUT to Discord's gateway and holds a websocket, so
there is no public hostname, no ingress rule, no Access policy to keep correct
and no inbound surface at all. It also collapses both wanted behaviours into one
process: slash commands for asking, and a scheduled check for being told.

READ ONLY, and more emphatically than elsewhere in this repo, because this is
the one component that takes instructions from a chat window. There are no
write or transaction commands and none should ever be added.

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
import logging
import os
import sys
from datetime import UTC, datetime
from datetime import time as dtime

import discord
from discord import app_commands
from discord.ext import tasks

from . import config

log = logging.getLogger("combine.bot")

# The scheduled check. Sunday morning Pacific, before the early kickoffs, late
# enough that Saturday's injury news has landed.
CHECK_AT = dtime(hour=15, minute=30, tzinfo=UTC)   # 08:30 PT
CHECK_DAYS = (6,)     # Sunday, as isoweekday


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

def _distribution():
    """Outcome history, or None. Optional by design: a missing database costs
    the floor and ceiling columns, not the answer."""
    from . import db
    from .pipeline.distribution import load as load_dist

    try:
        with db.connect(readonly=True) as conn:
            dist = load_dist(conn, config.SEASON - 1)
        return None if dist.empty else dist
    except Exception as exc:
        log.warning("no outcome history: %s", exc)
        return None


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


def build_week(league: str, week: int | None = None) -> list[str]:
    from . import discord_out
    from .platforms import client_for

    client = client_for(league)
    matchup = client.matchup(week)
    stamp = datetime.now().astimezone().strftime("%H:%M %Z")
    return discord_out.week_message(matchup, client.roster_slots(),
                                    config.get_league(league).name, stamp)


def build_startsit(league: str, week: int | None = None) -> tuple[list[str], bool]:
    """(messages, whether there is anything worth interrupting for)."""
    from . import discord_out
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
    messages = discord_out.startsit_message(
        matchup, calls, hurt, usage, ids, in_season,
        config.get_league(league).name, slots, dist)
    return messages, discord_out.has_news(calls, hurt, gain)


def build_compare(league: str, a: str, b: str, week: int | None = None) -> list[str]:
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
        return [" ".join(x for x in (err_a, err_b) if x)]
    usage, ids, in_season = _pff()
    text = head_to_head(first, second, usage, ids, in_season, matchup.week)
    return [f"```\n{text}\n```"]


def build_health() -> list[str]:
    from .platforms import client_for

    lines = []
    for slug, cfg in config.leagues().items():
        try:
            lines.append(f"✅ `{slug}` ({cfg.platform}) {client_for(slug).ping()}")
        except Exception as exc:
            lines.append(f"❌ `{slug}` ({cfg.platform}) {type(exc).__name__}: {exc}")
    return ["**Health**\n" + "\n".join(lines)]


def build_glossary() -> list[str]:
    from . import discord_out
    from .pipeline.usage import GLOSSARY, OUTCOME_GLOSSARY

    blocks = []
    for title, entries in GLOSSARY:
        blocks.append(f"**{title}**\n"
                      + "\n".join(f"`{tok}` {meaning}" for tok, meaning in entries))
    blocks.append("**Outcome columns**\n"
                  + "\n".join(f"`{tok}` {meaning}" for tok, meaning in OUTCOME_GLOSSARY))
    return discord_out.chunk(blocks)


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
        guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
        if guild:
            # Guild-scoped commands appear immediately; global ones can take an
            # hour to propagate, which makes iterating miserable.
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if CHANNEL_ID:
            weekly_check.start(self)


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


async def respond(interaction: discord.Interaction, work, *args):
    """Defer, do the blocking work in a thread, then follow up.

    Both halves matter: the three second deadline, and keeping synchronous I/O
    off the event loop so the gateway heartbeat survives.
    """
    await interaction.response.defer(thinking=True)
    try:
        messages = await asyncio.to_thread(work, *args)
    except Exception as exc:
        log.exception("command failed")
        await interaction.followup.send(
            f"`{type(exc).__name__}: {exc}`\nIf this is a 401 or an empty league, "
            f"the ESPN cookies expired: run `python scripts/refresh_espn_cookies.py`.")
        return
    if isinstance(messages, tuple):
        messages = messages[0]
    for message in messages or ["(nothing to report)"]:
        await interaction.followup.send(message)


@client.tree.command(description="This week's lineup, with projections and opponents")
@app_commands.describe(league="Which league", week="Week number, blank for current")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def week(interaction: discord.Interaction, league: str, week: int | None = None):
    await respond(interaction, build_week, league, week)


@client.tree.command(description="Start/sit calls: only the slots with a real question")
@app_commands.describe(league="Which league", week="Week number, blank for current")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def startsit(interaction: discord.Interaction, league: str,
                   week: int | None = None):
    await respond(interaction, lambda lg, wk: build_startsit(lg, wk)[0], league, week)


@client.tree.command(description="Two players head to head, in one league")
@app_commands.describe(league="Which league", player_a="First player",
                       player_b="Second player")
@app_commands.choices(league=LEAGUE_CHOICES)
@owner_only()
async def compare(interaction: discord.Interaction, league: str,
                  player_a: str, player_b: str):
    await respond(interaction, build_compare, league, player_a, player_b)


@client.tree.command(description="What the Role and outcome numbers mean")
@owner_only()
async def glossary(interaction: discord.Interaction):
    await respond(interaction, build_glossary)


@client.tree.command(description="Per-league connection status")
@owner_only()
async def health(interaction: discord.Interaction):
    await respond(interaction, build_health)


@tasks.loop(time=CHECK_AT)
async def weekly_check(bot: discord.Client):
    """Post only when there is something to say.

    Silence is the feature. A correct lineup should produce no message at all,
    because a bot that says "nothing to report" every week is a bot you mute and
    then miss the one week it mattered.
    """
    if datetime.now(UTC).isoweekday() not in CHECK_DAYS:
        return
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
            messages, newsworthy = await asyncio.to_thread(build_startsit, slug, None)
        except Exception as exc:
            log.exception("weekly check failed for %s", slug)
            await channel.send(f"⚠️ `{slug}` check failed: `{type(exc).__name__}: {exc}`")
            continue
        if not newsworthy:
            log.info("%s: nothing worth posting", slug)
            continue
        for message in messages:
            await channel.send(message)


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
