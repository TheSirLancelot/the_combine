"""combine <command>"""

from __future__ import annotations

import sys

from . import config, db


def doctor() -> int:
    print(f"repo      {config.REPO_ROOT}")
    print(f"season    {config.SEASON}")
    print(f"db        {config.DB_PATH} {'(exists)' if config.DB_PATH.exists() else '(missing, run: combine init)'}")

    missing = config.missing_env()
    print(f"env       {'all set' if not missing else 'MISSING: ' + ', '.join(missing)}")

    lg = config.leagues()
    print(f"leagues   {len(lg)} configured")
    for slug, cfg in lg.items():
        pick = f"pick {cfg.draft_slot}" if cfg.draft_slot else "pick ?"
        print(f"          {slug:6s} {cfg.platform:6s} id={cfg.league_id:12s} "
              f"{pick:8s} {cfg.name}")

    if "--live" in sys.argv:
        from .platforms import client_for

        print("live      probing platforms")
        for slug, cfg in lg.items():
            if not config.platform_ready(cfg.platform):
                print(f"          {slug:6s} SKIP {cfg.platform} credentials not set yet")
                continue
            try:
                print(f"          {slug:6s} OK   {client_for(slug).ping()}")
            except Exception as exc:
                print(f"          {slug:6s} FAIL {type(exc).__name__}: {exc}")
    return 1 if missing else 0


def init() -> int:
    db.ensure_schema()
    with db.connect() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"schema ready at {config.DB_PATH}")
    print("tables: " + ", ".join(t for t in tables if not t.startswith("sqlite_")))
    return 0


def try_tools() -> int:
    """Exercise the MCP tools locally, exactly as Claude would see them.
      combine try                 -> list leagues
      combine try settings rcl
      combine try pool rcl RB 15
      combine try roster dmwd
    """
    from . import server

    def fn(tool):
        """@mcp.tool returns a bare function in some FastMCP versions and a
        FunctionTool wrapper in others. Work with either."""
        return getattr(tool, "fn", tool)

    args = sys.argv[2:]
    what = args[0] if args else "leagues"
    rest = args[1:]
    if what == "leagues":
        print(fn(server.list_leagues)())
    elif what == "settings":
        print(fn(server.get_league_settings)(rest[0]))
    elif what == "roster":
        print(fn(server.get_my_roster)(rest[0]))
    elif what == "board":
        league = rest[0]
        pos = rest[1] if len(rest) > 1 else ""
        limit = int(rest[2]) if len(rest) > 2 else 20
        print(fn(server.get_draft_board)(league, pos, limit))
    elif what == "plan":
        # combine try plan <league> <pick-on-clock> [POS] [limit] [--slot=N]
        league = rest[0]
        args = rest[1:]
        slot = 0
        for a in list(args):
            if a.startswith("--slot="):
                slot = int(a.split("=", 1)[1])
                args.remove(a)
        nums = [int(a) for a in args if a.isdigit()]
        pos = next((a for a in args if not a.isdigit()), "")
        on_clock = nums[0] if nums else 1
        limit = nums[1] if len(nums) > 1 else 12
        print(fn(server.get_draft_plan)(league, on_clock, slot, pos, limit))
    elif what == "crosswalk":
        from .pipeline.board import build as build_board
        from .platforms import client_for
        league = rest[0]
        pool = client_for(league).free_agents(limit=int(rest[1]) if len(rest) > 1 else 300)
        rows, counts = build_board(league, pool)
        print(f"{league}: {len(pool)} espn players")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {k:12s} {v}")
        fuzzy = [r for r in rows if r.how in ("fuzzy", "pos", "team", "nickname")]
        if fuzzy:
            print("  --- non-exact matches, eyeball these ---")
            for r in fuzzy:
                print(f"  {r.how:6s} {r.state.name:24s} ({r.state.pos:<4} {r.state.team or '--'})"
                      f" -> {r.pff_name}")
        bad = [{"espn_name": r.state.name, "espn_pos": r.state.pos,
                "espn_team": r.state.team or "", "reason": r.how, "pff_name": ""}
               for r in rows if r.how in ("unmatched", "ambiguous")]
        if bad:
            from .pipeline.crosswalk import write_unmatched
            out = config.DATA_DIR / f"unmatched_{league}.csv"
            write_unmatched(bad, out)
            print(f"  wrote {out}")
    elif what == "needs":
        print(fn(server.get_needs)(rest[0], int(rest[1]) if len(rest) > 1 else 10))
    elif what == "notes":
        print(fn(server.get_player_notes)(rest[0], " ".join(rest[1:])))
    elif what == "health":
        print(fn(server.health_check)())
    else:
        print(f"unknown: {what}", file=sys.stderr)
        return 2
    return 0


def week() -> int:
    """combine week <league> [week]

    The in-season view: my starters and bench for one week with ESPN's weekly
    projections, problem starters, and any bench player outprojecting a starter
    he is slot eligible for.
    """
    from .pipeline.lineup import render
    from .platforms import client_for

    args = sys.argv[2:]
    if not args:
        print("usage: combine week <league> [week]", file=sys.stderr)
        return 2
    league = args[0]
    wk = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    c = client_for(league)
    print(render(c.matchup(wk), c.roster_slots(), config.get_league(league).name))
    return 0


def pff_ids() -> int:
    """combine pffids <league> [pool-size]

    Resolve this league's players onto PFF's stable player_id and store the
    mapping. Run it once, and again when rosters churn. Start/sit joins on the
    stored id rather than re-matching names on every call.
    """
    from .pipeline.crosswalk import (
        PFF_IDS,
        directory,
        resolve_by_lookup,
        resolve_ids,
        save_ids,
        write_unmatched,
    )
    from .pipeline.providers.pff_api import PffApi
    from .platforms import client_for

    args = sys.argv[2:]
    if not args:
        print("usage: combine pffids <league> [pool-size]", file=sys.stderr)
        return 2
    league = args[0]
    pool_size = int(args[1]) if len(args) > 1 and args[1].isdigit() else 300

    api = PffApi()
    state = api.season_state()
    print(f"pff calendar: {state.describe()}")

    people = directory(api)
    print(f"pff directory: {len(people)} charted players")

    c = client_for(league)
    espn = c.my_roster() + c.free_agents(position=None, limit=pool_size)
    rows, misses = resolve_ids(espn, people)
    # Second pass: anyone PFF never charted last season is absent from the
    # directory but still has an id. Rookies, mostly.
    found, misses = resolve_by_lookup(api, misses)
    rows += found
    stored = save_ids(rows)

    total = len(rows) + len([m for m in misses
                             if m["espn_pos"].upper() not in ("D/ST", "DST", "DEF")])
    print(f"{league}: {len(rows)} of {total} resolved "
          f"({100 * len(rows) / total:.0f}%), {stored} stored -> {PFF_IDS}")
    by_how: dict[str, int] = {}
    for r in rows:
        by_how[r["how"]] = by_how.get(r["how"], 0) + 1
    for how, n in sorted(by_how.items(), key=lambda kv: -kv[1]):
        print(f"  {how:10s} {n}")
    # PFF has no team-defense entity in these facets, so every D/ST is
    # absent by design rather than by failure. Counting them as misses would
    # bury the real ones.
    dst = [m for m in misses if m["espn_pos"].upper() in ("D/ST", "DST", "DEF")]
    misses = [m for m in misses if m not in dst]
    if dst:
        print(f"  {'d/st':10s} {len(dst)} skipped, PFF has no team-defense player")
    if misses:
        out = config.DATA_DIR / f"unmatched_pff_ids_{league}.csv"
        write_unmatched(misses, out)
        print(f"  unmatched  {len(misses)}  -> {out}")
        for m in misses[:10]:
            print(f"    {m['reason']:10s} {m['espn_name']} ({m['espn_pos']} {m['espn_team']})")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "week":
        return week()
    if cmd == "pffids":
        return pff_ids()
    if cmd == "init":
        return init()
    if cmd == "try":
        return try_tools()
    if cmd == "serve":
        from .server import main as serve

        serve()
        return 0
    print("usage: combine [doctor [--live] | init | week <league> [week] | "
          "pffids <league> | try ... | serve]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
