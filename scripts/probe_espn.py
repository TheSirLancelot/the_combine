# TODO: Fix errors

#!/usr/bin/env python3
"""Dump the real shape of espn-api objects so the adapter is written against
actual field names instead of guesses. Read-only. Writes probe_espn_output.txt.

  uv run python scripts/probe_espn.py rcl
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from combine.config import SEASON, get_league
from combine.platforms import client_for

OUT = ROOT / "probe_espn_output.txt"
MAXLEN = 220
lines: list[str] = []


def p(s: str = "") -> None:
    print(s)
    lines.append(s)


def trunc(v) -> str:
    s = repr(v)
    return s if len(s) <= MAXLEN else s[:MAXLEN] + f"... <{len(s)} chars>"


def describe(obj, label: str, skip_private: bool = True) -> None:
    p(f"\n--- {label} :: {type(obj).__module__}.{type(obj).__name__}")
    attrs = getattr(obj, "__dict__", None)
    if attrs is None:
        p(f"    (no __dict__) {trunc(obj)}")
        return
    for k in sorted(attrs):
        if skip_private and k.startswith("_"):
            continue
        p(f"    {k:24s} = {trunc(attrs[k])}")


def section(name, fn):
    p(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    try:
        fn()
    except Exception as exc:
        p(f"!! {type(exc).__name__}: {exc}")


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "rcl"
    cfg = get_league(slug)
    lg = client_for(slug).league
    p(f"league {slug}  season {SEASON}  espn id {cfg.league_id}  current_week {lg.current_week}")

    section("SETTINGS", lambda: describe(lg.settings, "settings"))

    def scoring():
        s = lg.settings
        for attr in ("scoring_format", "scoring_settings", "roster_slots", "position_slot_counts"):
            if hasattr(s, attr):
                val = getattr(s, attr)
                p(f"\n  settings.{attr} type={type(val).__name__}")
                if isinstance(val, dict):
                    for k, v in list(val.items())[:40]:
                        p(f"    {k!r:28s} -> {trunc(v)}")
                elif isinstance(val, list):
                    for v in val[:40]:
                        p(f"    {trunc(v)}")

    section("SCORING / ROSTER SLOTS", scoring)

    def my_team():
        team = next((t for t in lg.teams if str(t.team_id) == str(cfg.team_id)), None)
        if team is None:
            p(
                f"!! team_id {cfg.team_id} not found. available: "
                + ", ".join(f"{t.team_id}={t.team_name}" for t in lg.teams)
            )
            return
        describe(team, "my team")
        if team.roster:
            describe(team.roster[0], "roster[0] (Player)")
            pl = team.roster[0]
            p(f"\n  stats keys: {list(getattr(pl, 'stats', {}))[:12]}")
            for wk, blob in list(getattr(pl, "stats", {}).items())[:2]:
                p(f"\n  stats[{wk!r}]:")
                for k, v in (blob or {}).items():
                    p(f"    {k:22s} = {trunc(v)}")

    section("MY TEAM + PLAYER SHAPE", my_team)

    def fa():
        players = lg.free_agents(size=3)
        p(f"  returned {len(players)}")
        if players:
            describe(players[0], "free_agents[0]")

    section("FREE AGENTS", fa)

    def box():
        wk = max(lg.current_week, 1)
        scores = lg.box_scores(wk)
        p(f"  week {wk}, {len(scores)} box scores")
        if scores:
            describe(scores[0], "box_scores[0]", skip_private=False)

    section("BOX SCORES", box)

    OUT.write_text("\n".join(lines) + "\n")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
