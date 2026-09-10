"""Shaping the tool's output for a phone screen.

The CLI renderers are built for a terminal and run to 130 characters on their
widest line. Discord wraps a code block rather than scrolling it, so anything
past about sixty characters turns into mush on a phone, which is where this will
actually be read. Measured before writing: the week view is 1353 characters over
27 lines, so total length is not the problem. Line width is.

So tables are re-rendered narrow and go in code blocks, where alignment matters,
and prose stays as plain markdown so Discord can wrap it properly. Two different
jobs, two different treatments.
"""

from __future__ import annotations

from .pipeline import startsit as startsit_mod
from .pipeline.lineup import BAD_STATUS, near_misses, optimal_moves, order_starters, problems, split
from .platforms import Matchup, WeeklyPlayer

# Discord's hard cap is 2000; leave room for the code fence and a stray emoji.
LIMIT = 1900
NAME = 16          # widest a player name gets before truncation
CODE = "```"


def chunk(blocks: list[str]) -> list[str]:
    """Pack pre-formed blocks into as few messages as possible.

    Blocks are never split internally: a half a table is worse than a second
    message, and a code fence broken across messages renders as garbage.
    """
    out: list[str] = []
    current = ""
    for block in blocks:
        if not block:
            continue
        if len(block) >= LIMIT:          # a single oversized block goes alone
            if current:
                out.append(current)
                current = ""
            out.append(block[:LIMIT])
            continue
        if len(current) + len(block) + 2 > LIMIT:
            out.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        out.append(current)
    return out


def _row(p: WeeklyPlayer, show_actual: bool) -> str:
    note = " ".join(x for x in (p.status if p.status != "OK" else "",
                                "LOCK" if p.locked and not p.played else "") if x)
    return (f"{p.slot[:8]:<8} {p.pos[:3]:<3} {p.name[:NAME]:<{NAME}} "
            f"{(p.opponent or '--')[:6]:<6} {p.projected:>5.1f}"
            + (f" {p.actual:>5.1f}" if show_actual else "")
            + (f"  {note}" if note else ""))


def _table(title: str, players: list[WeeklyPlayer], show_actual: bool) -> str:
    head = (f"{'SLOT':<8} {'POS':<3} {'PLAYER':<{NAME}} {'OPP':<6} {'PROJ':>5}"
            + (f" {'ACT':>5}" if show_actual else ""))
    rows = "\n".join(_row(p, show_actual) for p in players)
    return f"**{title}**\n{CODE}\n{head}\n{rows}\n{CODE}"


def week_message(m: Matchup, slots: dict[str, int], league_name: str,
                 pulled_at: str = "") -> list[str]:
    starters, bench = split(m.my_lineup)
    starters = order_starters(starters, slots)
    played = any(p.played for p in m.my_lineup)

    header = f"**{league_name} — week {m.week}**"
    if m.their_lineup:
        header += (f"\n{m.my_proj:.1f} vs {m.their_team} {m.their_proj:.1f}"
                   f"  ({m.my_proj - m.their_proj:+.1f})")
    else:
        header += f"\n{m.my_proj:.1f} projected from your starters"
    if pulled_at:
        header += f"\n_projections as of {pulled_at}_"

    blocks = [header,
              _table("Starters", starters, played),
              _table("Bench", bench, played)]

    hurt = problems(starters)
    if hurt:
        blocks.append("**Cannot play, still starting**\n"
                      + "\n".join(f"- `{p.slot}` {p.name} "
                                  f"({'bye' if p.on_bye else p.status})"
                                  for p in hurt))
    return chunk(blocks)


def startsit_message(m: Matchup, calls, hurt, usage, ids, in_season: bool,
                     league_name: str, slots: dict[str, int], dist=None) -> list[str]:
    """The decision message. Prose rather than a table: every line here is a
    sentence, and sentences should wrap."""
    blocks = [f"**{league_name} — week {m.week} start/sit**"]

    if slots:
        add, drop, gain = optimal_moves(m.my_lineup, slots)
        if gain > 0.05 and add:
            lines = [f"**Lineup is {gain:.1f} projected points short of optimal**"]
            lines += [f"- START **{a.name}** ({a.pos}) {a.projected:.1f}" for a in add]
            lines += [f"- BENCH {d.name} ({d.pos}) {d.projected:.1f}"
                      + (" _cannot play_" if d.on_bye or d.status in BAD_STATUS else "")
                      for d in drop]
            lines.append("_exact slot assignment, not a prediction_")
            blocks.append("\n".join(lines))

    if hurt:
        blocks.append("**Cannot play, still starting**\n"
                      + "\n".join(f"- `{p.slot}` {p.name} "
                                  f"({'bye' if p.on_bye else p.status})" for p in hurt))

    for c in sorted(calls, key=lambda x: -x.proj_edge):
        label = "SWAP" if c.verdict == "CLEAR" else c.verdict
        lines = [f"**{label} · {c.slot} · +{c.proj_edge:.1f} projected**",
                 f"IN  **{c.bench.name}** {c.bench.projected:.1f} {c.bench.opponent}",
                 f"OUT {c.starter.name} {c.starter.projected:.1f} {c.starter.opponent}"]
        for player, tag in ((c.bench, "in"), (c.starter, "out")):
            u = startsit_mod.usage_mod.for_espn(usage, ids, player.player_id)
            if u:
                lines.append(f"_{tag}:_ `{u.line(player.pos)}`")
        if c.opp_edge is not None:
            word = "more" if c.opp_edge > 0 else "fewer"
            lines.append(f"_usage: {abs(c.opp_edge):.1f} {word} opportunities a game "
                         f"for {c.bench.name}_")
        for reason in c.reasons:
            lines.append(f"_{reason}_")
        blocks.append("\n".join(lines))

    if not calls and not hurt:
        starters, bench = split(m.my_lineup)
        close = near_misses(starters, bench, dist)
        lines = ["No start/sit questions: no bench player is projected far enough "
                 "ahead of a starter he could replace."]
        for c in close:
            lines.append(f"- {c.bench.name} {c.bench.projected:.1f} needs "
                         f"**{c.short_by:.1f} more** to weigh against "
                         f"{c.starter.name} {c.starter.projected:.1f} "
                         f"(that pair needs a {c.needed:.1f} point edge)")
        blocks.append("\n".join(lines))

    if not in_season:
        blocks.append("_usage numbers are last season's, a prior rather than "
                      "evidence about this week_")
    return chunk(blocks)


def has_news(calls, hurt, gain: float) -> bool:
    """Whether a scheduled check has anything worth interrupting you for.

    Silence is the feature. Most weeks a correct lineup should produce no
    message at all, and a bot that posts "nothing to report" every Sunday is a
    bot you mute.
    """
    return bool(calls or hurt or gain > 0.05)


def scoreboard_message(games, missing) -> list[str]:
    """Scores, narrow enough for a phone.

    The terminal version runs to 100 characters with both teams on one line.
    That wraps into mush here, so each matchup is two lines with the score
    right-aligned, and the shared numbers move into a single trailing line.
    """
    if not games and not missing:
        return ["no leagues configured"]

    blocks: list[str] = []
    mine = [g for g in games if g.involves_me]
    if mine:
        lines = ["**Your matchups**"]
        for g in mine:
            me, them = g.me, g.them
            verb = ("won by" if g.final and g.margin > 0 else
                    "lost by" if g.final and g.margin < 0 else
                    "tied" if g.final else
                    "up" if g.margin > 0 else "down" if g.margin < 0 else "level")
            tail = "" if verb in ("level", "tied") else f" {abs(g.margin):.1f}"
            lines.append(f"**{g.league_name}** · {verb}{tail}")
            lines.append(f"`{me.score:>6.1f}` you   ·   `{them.score:>6.1f}` "
                         f"{them.team[:18]}")
            if not g.final:
                lines.append(f"_ESPN projects {me.projected:.1f} to "
                             f"{them.projected:.1f} ({g.projected_margin:+.1f}), "
                             f"{me.yet_to_play} of yours left vs "
                             f"{them.yet_to_play}_")
        blocks.append("\n".join(lines))

    for slug in dict.fromkeys(g.league for g in games):
        rows = [g for g in games if g.league == slug]
        table = [f"{'TEAM':<18} {'SCORE':>6} {'PROJ':>6}"]
        for g in rows:
            flag = "*" if g.involves_me else " "
            for side in (g.home, g.away):
                table.append(f"{flag}{side.team[:17]:<17} {side.score:>6.1f} "
                             f"{side.projected:>6.1f}")
            table.append("")
        blocks.append(f"**{rows[0].league_name} — week {rows[0].week}**\n"
                      f"{CODE}\n" + "\n".join(table).rstrip() + f"\n{CODE}")

    for item in missing:
        blocks.append(f"_{item.league_name}: not on the scoreboard. {item.reason}_")
    return chunk(blocks)
