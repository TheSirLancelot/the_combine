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
        print(f"          {slug:6s} {cfg.platform:6s} id={cfg.league_id:12s} {cfg.name}")

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
    elif what == "pool":
        league = rest[0]
        pos = rest[1] if len(rest) > 1 else ""
        limit = int(rest[2]) if len(rest) > 2 else 15
        print(fn(server.get_draft_pool)(league, pos, limit))
    elif what == "board":
        league = rest[0]
        pos = rest[1] if len(rest) > 1 else ""
        limit = int(rest[2]) if len(rest) > 2 else 20
        print(fn(server.get_draft_board)(league, pos, limit))
    elif what == "plan":
        league = rest[0]
        slot = int(rest[1])
        on_clock = int(rest[2])
        extra = rest[3:]
        pos = next((a for a in extra if not a.isdigit()), "")
        limit = next((int(a) for a in extra if a.isdigit()), 12)
        print(fn(server.get_draft_plan)(league, slot, on_clock, pos, limit))
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
    elif what == "notes":
        print(fn(server.get_player_notes)(rest[0], " ".join(rest[1:])))
    elif what == "health":
        print(fn(server.health_check)())
    else:
        print(f"unknown: {what}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "init":
        return init()
    if cmd == "try":
        return try_tools()
    if cmd == "serve":
        from .server import main as serve

        serve()
        return 0
    print("usage: combine [doctor [--live] | init | try ... | serve]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
