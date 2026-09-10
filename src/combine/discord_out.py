"""Shaping the tool's output for Discord.

Three rules drive everything in here.

Alignment needs monospace and embeds are not monospace, so every table stays
inside a code block. Discord wraps a code block rather than scrolling it, so a
table past about forty-five characters turns to mush on a phone, which is where
this actually gets read. Widths here are measured, not guessed.

Everything that is not a table is better as embed structure than as text. A
score line reads better as three columns than as a sentence, and a caveat reads
better as a titled field than as an italic afterthought.

The colour of the left bar carries severity, which is the one piece of
decoration that does real work: green means nothing to do, amber means there is
a decision waiting, red means someone who cannot play is in the lineup. It is
readable at a glance from a notification shade without opening anything.
"""

from __future__ import annotations

import discord

from .pipeline import startsit as startsit_mod
from .pipeline.lineup import BAD_STATUS, near_misses, optimal_moves, order_starters, problems, split
from .platforms import Matchup, WeeklyPlayer

# Discord's caps, with headroom for a stray emoji or a truncation marker.
LIMIT = 1900          # plain message content
DESC = 3900           # embed description
FIELD = 1000          # embed field value
FIELDS = 25           # fields per embed
# Discord's real cap is 6000 characters across the whole embed. The budget is
# lower because footers are set AFTER the fields are added, so `len(embed)` at
# the time a field is measured does not yet include one.
TOTAL = 5500          # every character in one embed, added up
NAME = 16             # widest a player name gets before truncation
CODE = "```"

# Severity, not palette. See the module docstring.
GOOD = 0x2ECC71       # nothing to do
INFO = 0x5865F2       # here is your information
WARN = 0xF1C40F       # a decision is waiting
BAD = 0xE74C3C        # something is actually wrong
DEAD = 0x4F545C       # cannot answer: not configured, not supported


def short_name(name: str, width: int = NAME) -> str:
    """"DeForest Buckner" -> "D. Buckner". A hard truncation to column width
    gives "DeForest Buck", which is both ugly and ambiguous between two players
    with the same first name."""
    parts = (name or "").split()
    if len(parts) > 1:
        name = f"{parts[0][0]}. {' '.join(parts[1:])}"
    return name[:width]


def code(body: str) -> str:
    return f"{CODE}\n{body}\n{CODE}"


def field(embed: discord.Embed, name: str, value: str, inline: bool = False) -> bool:
    """Add a field if it fits. Returns whether it went in.

    Three caps, and all three are hard: 25 fields, 1024 characters in a value,
    and 6000 characters in the whole embed counted together. Discord rejects an
    oversized embed outright, taking the entire message with it, so the last one
    is the dangerous one -- it is invisible until a long week trips it. Found by
    a test rather than in production, which is the only reason it is handled
    here rather than being a Sunday morning with no post.
    """
    if len(embed.fields) >= FIELDS:
        return False
    name = name[:256]
    if len(value) > FIELD:
        value = value[:FIELD - 20].rstrip() + "\n… truncated"
    room = TOTAL - len(embed) - len(name)
    if room < 40:                      # not enough left for a useful field
        return False
    if len(value) > room:
        value = value[:room - 12].rstrip() + "\n…"
    embed.add_field(name=name, value=value or "--", inline=inline)
    return True


def chunk(blocks: list[str], limit: int = LIMIT) -> list[str]:
    """Pack pre-formed blocks into as few pieces as possible.

    Blocks are never split internally: half a table is worse than a second
    message, and a code fence broken across messages renders as garbage.
    """
    out: list[str] = []
    current = ""
    for block in blocks:
        if not block:
            continue
        if len(block) >= limit:          # a single oversized block goes alone
            if current:
                out.append(current)
                current = ""
            out.append(block[:limit])
            continue
        if len(current) + len(block) + 2 > limit:
            out.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        out.append(current)
    return out


def embed_text(embed: discord.Embed) -> str:
    """Flatten an embed back to text, for `--dry-run` and for tests.

    Nothing in Discord needs this. It exists so `combine notify --dry-run` can
    still show you what would be posted without a network call, which is the
    only way to check the wording of a message that fires once a week.
    """
    out = []
    if embed.title:
        out.append(f"== {embed.title}")
    if embed.description:
        out.append(embed.description)
    for f in embed.fields:
        out.append(f"-- {f.name}\n{f.value}")
    if embed.footer and embed.footer.text:
        out.append(f"({embed.footer.text})")
    return "\n".join(out)


# --- the week ---------------------------------------------------------------

def _row(p: WeeklyPlayer, show_actual: bool) -> str:
    note = " ".join(x for x in (p.status if p.status != "OK" else "",
                                "LOCK" if p.locked and not p.played else "") if x)
    return (f"{p.slot[:8]:<8} {p.pos[:3]:<3} {p.name[:NAME]:<{NAME}} "
            f"{(p.opponent or '--')[:6]:<6} {p.projected:>5.1f}"
            + (f" {p.actual:>5.1f}" if show_actual else "")
            + (f"  {note}" if note else ""))


def _table(players: list[WeeklyPlayer], show_actual: bool) -> str:
    head = (f"{'SLOT':<8} {'POS':<3} {'PLAYER':<{NAME}} {'OPP':<6} {'PROJ':>5}"
            + (f" {'ACT':>5}" if show_actual else ""))
    rows = "\n".join(_row(p, show_actual) for p in players)
    return code(f"{head}\n{rows}")


def _cannot_play(players) -> str:
    return "\n".join(f"`{p.slot}` **{p.name}** — {'bye' if p.on_bye else p.status}"
                     for p in players)


def week_embeds(m: Matchup, slots: dict[str, int], league_name: str,
                pulled_at: str = "") -> list[discord.Embed]:
    """Three stacked cards: the matchup, the starters, the bench.

    Not one embed, and the reason is Discord's render order. Within an embed the
    description always comes before the fields, so a single embed puts the
    lineup table above the score, and the score is the thing he opened the
    message for. Separate embeds stack in the order given, so the number lands
    first and the tables follow.
    """
    starters, bench = split(m.my_lineup)
    starters = order_starters(starters, slots)
    played = any(p.played for p in m.my_lineup)
    hurt = problems(starters)
    colour = BAD if hurt else INFO

    head = discord.Embed(title=f"Week {m.week} · {league_name}", colour=colour)
    if m.their_lineup:
        head.add_field(name="You", value=f"**{m.my_proj:.1f}**", inline=True)
        head.add_field(name=(m.their_team or "Opponent")[:24],
                       value=f"**{m.their_proj:.1f}**", inline=True)
        head.add_field(name="Margin",
                       value=f"**{m.my_proj - m.their_proj:+.1f}**", inline=True)
    else:
        # The hand-entered league has no opponent lineup, so a margin would be
        # fiction rather than a missing feature.
        head.add_field(name="Projected from your starters",
                       value=f"**{m.my_proj:.1f}**", inline=True)
    if hurt:
        field(head, "⚠️ Cannot play, still starting", _cannot_play(hurt))
    if pulled_at:
        head.set_footer(text=f"projections as of {pulled_at}")

    return [head,
            discord.Embed(title="Starters", colour=colour,
                          description=_table(starters, played)[:DESC]),
            discord.Embed(title="Bench", colour=colour,
                          description=_table(bench, played)[:DESC])]


# --- start/sit --------------------------------------------------------------

def startsit_embeds(m: Matchup, calls, hurt, usage, ids, in_season: bool,
                    league_name: str, slots: dict[str, int],
                    dist=None) -> list[discord.Embed]:
    calls = sorted(calls, key=lambda x: -x.proj_edge)
    add = drop = ()
    gain = 0.0
    if slots:
        add, drop, gain = optimal_moves(m.my_lineup, slots)

    colour = BAD if hurt else WARN if (calls or gain > 0.05) else GOOD
    e = discord.Embed(title=f"Week {m.week} start/sit · {league_name}", colour=colour)

    if gain > 0.05 and add:
        lines = [f"- START **{a.name}** ({a.pos}) {a.projected:.1f}" for a in add]
        lines += [f"- BENCH {d.name} ({d.pos}) {d.projected:.1f}"
                  + (" *cannot play*" if d.on_bye or d.status in BAD_STATUS else "")
                  for d in drop]
        lines.append("*exact slot assignment, not a prediction*")
        field(e, f"📉 {gain:.1f} projected points short of optimal", "\n".join(lines))

    if hurt:
        field(e, "⚠️ Cannot play, still starting", _cannot_play(hurt))

    for c in calls:
        label = "SWAP" if c.verdict == "CLEAR" else c.verdict
        lines = [f"IN  **{c.bench.name}** {c.bench.projected:.1f} {c.bench.opponent}",
                 f"OUT {c.starter.name} {c.starter.projected:.1f} {c.starter.opponent}"]
        for player, tag in ((c.bench, "in"), (c.starter, "out")):
            u = startsit_mod.usage_mod.for_espn(usage, ids, player.player_id)
            if u:
                lines.append(f"*{tag}:* `{u.line(player.pos)}`")
        if c.opp_edge is not None:
            word = "more" if c.opp_edge > 0 else "fewer"
            lines.append(f"*usage: {abs(c.opp_edge):.1f} {word} opportunities a "
                         f"game for {c.bench.name}*")
        lines += [f"*{reason}*" for reason in c.reasons]
        field(e, f"{label} · {c.slot} · +{c.proj_edge:.1f} projected", "\n".join(lines))

    if not calls and not hurt:
        starters, bench = split(m.my_lineup)
        e.description = ("No start/sit questions: no bench player is projected "
                         "far enough ahead of a starter he could replace.")
        close = near_misses(starters, bench, dist)
        if close:
            field(e, "Closest to a question",
                  "\n".join(f"**{c.bench.name}** {c.bench.projected:.1f} needs "
                            f"**{c.short_by:.1f} more** to weigh against "
                            f"{c.starter.name} {c.starter.projected:.1f} "
                            f"(that pair needs {c.needed:.1f})" for c in close))

    if not in_season:
        e.set_footer(text="usage numbers are last season's, a prior rather than "
                          "evidence about this week")
    return [e]


def has_news(calls, hurt, gain: float) -> bool:
    """Whether a scheduled check has anything worth interrupting you for.

    Silence is the feature. Most weeks a correct lineup should produce no
    message at all, and a bot that posts "nothing to report" every Sunday is a
    bot you mute.
    """
    return bool(calls or hurt or gain > 0.05)


# --- scoreboard -------------------------------------------------------------

def scoreboard_embeds(games, missing) -> list[discord.Embed]:
    """Your matchup first, then the league table. Same reason as the week view:
    within one embed the table would render above the score."""
    if not games and not missing:
        return [discord.Embed(title="Scoreboard", colour=DEAD,
                              description="No leagues configured.")]

    out: list[discord.Embed] = []
    for slug in dict.fromkeys(g.league for g in games):
        rows = [g for g in games if g.league == slug]
        mine = next((g for g in rows if g.involves_me), None)
        colour = INFO
        if mine:
            colour = GOOD if mine.margin > 0 else BAD if mine.margin < 0 else INFO

        title = f"{rows[0].league_name} · week {rows[0].week}"
        if mine:
            verb = ("Won by" if mine.final and mine.margin > 0 else
                    "Lost by" if mine.final and mine.margin < 0 else
                    "Tied" if mine.final else
                    "Up" if mine.margin > 0 else
                    "Down" if mine.margin < 0 else "Level")
            tail = "" if verb in ("Level", "Tied") else f" {abs(mine.margin):.1f}"
            head = discord.Embed(title=title, colour=colour)
            head.add_field(name="You", value=f"**{mine.me.score:.1f}**", inline=True)
            head.add_field(name=mine.them.team[:24],
                           value=f"**{mine.them.score:.1f}**", inline=True)
            head.add_field(name=f"{verb}{tail}",
                           value=("*final*" if mine.final else
                                  f"*{mine.me.yet_to_play} left vs "
                                  f"{mine.them.yet_to_play}*"), inline=True)
            if not mine.final:
                # ESPN's projection, labelled as theirs. It moves during games.
                head.set_footer(text=f"ESPN projects {mine.me.projected:.1f} to "
                                     f"{mine.them.projected:.1f} "
                                     f"({mine.projected_margin:+.1f})")
            out.append(head)
            title = "Every matchup"

        table = [f"{'TEAM':<18} {'SCORE':>6} {'PROJ':>6}"]
        for g in rows:
            flag = "*" if g.involves_me else " "
            for side in (g.home, g.away):
                table.append(f"{flag}{side.team[:17]:<17} {side.score:>6.1f} "
                             f"{side.projected:>6.1f}")
            table.append("")
        out.append(discord.Embed(
            title=title, colour=colour,
            description=code("\n".join(table).rstrip())[:DESC]))

    for item in missing:
        out.append(discord.Embed(title=item.league_name, colour=DEAD,
                                 description=f"Not on the scoreboard. {item.reason}"))
    return out


# --- waivers ----------------------------------------------------------------

def waivers_embeds(candidates, league_name: str, week: int,
                   unavailable: str = "") -> list[discord.Embed]:
    """A table for the numbers, a field per row for the caveats.

    The caveats do not fit in a column and truncating one would leave a
    confident number with its qualifier cut off, which is the failure worth
    avoiding here.
    """
    title = f"Waiver wire · {league_name}" + (f" · week {week}" if week else "")
    if unavailable:
        return [discord.Embed(title=title, colour=DEAD, description=unavailable)]
    if not candidates:
        return [discord.Embed(
            title=title, colour=GOOD,
            description="Nobody on the wire improves the lineup. That is the "
                        "normal answer: the pool is unrostered for a reason.")]

    table = [f"{'':<3}{'ADD':<17}{'POS':<5}{'WK':>6}{'SZN':>7}"]
    for i, c in enumerate(candidates, start=1):
        flag = "*" if c.correction_carries_it else " "
        table.append(f"{str(i) + flag:<3}{short_name(c.name):<17}{c.pos[:4]:<5}"
                     f"{c.week_gain:>+6.1f}{c.season_cost:>+7.1f}")

    # Description before fields: the table is the part that must survive, so it
    # claims its room first and the notes fill what is left.
    e = discord.Embed(title=title, colour=INFO,
                      description=code("\n".join(table))[:DESC])
    shown = 0
    for i, c in enumerate(candidates, start=1):
        lines = [f"Drop **{c.drop_name}** ({c.drop_pos})"]
        if c.displaces:
            lines.append(f"Starts over {c.displaces}")
        note = c.blocked_note()
        if note:
            lines.append(f"🔒 {note}")
        if c.trades_down:
            lines.append("*Buys a week and pays for it later.*")
        if c.correction_carries_it:
            lines.append(f"⚠️ Only clears the bar because {c.pos} projections are "
                         f"corrected up {c.correction:.1f}, measured on "
                         f"{c.correction_n} player-weeks.")
        elif c.clears_despite_correction:
            lines.append(f"Clears even after {c.pos} is marked down "
                         f"{abs(c.correction):.1f}.")
        shown += field(e, f"{i}. {c.name} · {c.week_gain:+.1f} week · "
                          f"{c.season_cost:+.1f} season", "\n".join(lines))

    footer = ("WK is what the lineup gains this week, SZN what the drop costs or "
              "gains for the season. Each row is an alternative, not a sequence: "
              "every one is measured against the lineup you have now.")
    if shown < len(candidates):
        footer = (f"Notes shown for {shown} of {len(candidates)}; the rest are in "
                  f"`combine waivers`. ") + footer
    e.set_footer(text=footer[:2048])
    return [e]


# --- the small ones ---------------------------------------------------------

def compare_embeds(text: str, title: str = "Head to head") -> list[discord.Embed]:
    pieces = chunk([text], limit=DESC - 20)
    return [discord.Embed(title=title if i == 0 else f"{title} (cont.)",
                          colour=INFO, description=code(piece))
            for i, piece in enumerate(pieces)]


def message_embed(text: str, title: str = "", colour: int = DEAD) -> discord.Embed:
    """For the one-line answers: a bad player name, a league that cannot answer."""
    return discord.Embed(title=title or None, colour=colour, description=text[:DESC])


def health_embeds(rows: list[tuple[bool, str]]) -> list[discord.Embed]:
    broken = [line for ok, line in rows if not ok]
    e = discord.Embed(title="Health", colour=BAD if broken else GOOD)
    for ok, line in rows:
        slug, _, rest = line.partition(" ")
        field(e, f"{'✅' if ok else '❌'} {slug}", rest or "--")
    if broken:
        e.set_footer(text="A 401 is usually expired ESPN cookies in .env.")
    return [e]


def glossary_embeds(sections, outcome) -> list[discord.Embed]:
    out = []
    e = discord.Embed(title="Glossary", colour=INFO,
                      description="What the Role and outcome columns mean.")
    for title, entries in sections:
        if len(e.fields) >= FIELDS:
            out.append(e)
            e = discord.Embed(title="Glossary (cont.)", colour=INFO)
        field(e, title, "\n".join(f"`{tok}` {meaning}" for tok, meaning in entries))
    if outcome:
        if len(e.fields) >= FIELDS:
            out.append(e)
            e = discord.Embed(title="Glossary (cont.)", colour=INFO)
        field(e, "Outcome columns",
              "\n".join(f"`{tok}` {meaning}" for tok, meaning in outcome))
    out.append(e)
    return out
