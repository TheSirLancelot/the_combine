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
    from datetime import datetime

    c = client_for(league)
    m = c.matchup(wk)
    stamp = datetime.now().astimezone().strftime("%H:%M %Z")
    print(render(m, c.roster_slots(), config.get_league(league).name,
                 pulled_at=stamp))
    if config.get_league(league).platform == "manual":
        print("\n(hand-entered league: projections are ESPN stat lines priced by "
              "your scoring table, so they will not match Yahoo's display)")
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


def start_sit() -> int:
    """combine startsit [league...] [week]

    Only the slots with a real question: bench players outprojecting a starter
    they can legally replace, with PFF usage on both sides, plus anyone who
    cannot play and is still in the lineup.
    """
    from .pipeline.crosswalk import load_ids
    from .pipeline.providers.pff_api import PffApi
    from .pipeline.startsit import render, review
    from .pipeline.usage import load as load_usage
    from .platforms import client_for

    args = sys.argv[2:]
    # No league means every league, which is usually what you want on a Sunday.
    leagues = [a for a in args if not a.isdigit()] or list(config.leagues())
    wk = next((int(a) for a in args if a.isdigit()), None)
    unknown = [lg for lg in leagues if lg not in config.leagues()]
    if unknown:
        print(f"unknown league(s): {', '.join(unknown)}. configured: "
              f"{', '.join(config.leagues())}", file=sys.stderr)
        return 2

    ids = load_ids()
    if not ids:
        print("no PFF id crosswalk yet. run: combine pffids "
              + leagues[0], file=sys.stderr)
        return 2
    api = PffApi()
    state = api.season_state()
    usage = load_usage(api)
    # Outcome spread, shown as context and never used to rank. Ranking by it was
    # backtested and lost; see build guide item 6.
    dist = None
    try:
        from . import db
        from .pipeline.distribution import load as load_dist
        with db.connect(readonly=True) as conn:
            candidate = load_dist(conn, config.SEASON - 1)
        dist = None if candidate.empty else candidate
    except Exception:
        dist = None

    for i, league in enumerate(leagues):
        if i:
            print("\n" + "=" * 60)
        try:
            c = client_for(league)
            m = c.matchup(wk)
            calls, hurt = review(m, usage, ids, dist=dist)
            print(render(m, calls, hurt, usage, ids, state.in_season,
                         config.get_league(league).name, slots=c.roster_slots(),
                         dist=dist))
        except Exception as exc:
            print(f"{league}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if not state.in_season:
        print(f"\nusage is {state.stats_season}, read it as a prior")
    return 0


def compare() -> int:
    """combine compare <league> "Player A" "Player B" [week]

    Head to head for two players in one league, whether or not they are a legal
    swap for each other. Looks in your lineup first, then the free agent pool.
    """
    from .pipeline.crosswalk import load_ids
    from .pipeline.providers.pff_api import PffApi
    from .pipeline.startsit import head_to_head
    from .pipeline.usage import load as load_usage
    from .platforms import client_for

    args = sys.argv[2:]
    if len(args) < 3:
        print('usage: combine compare <league> "Player A" "Player B" [week]',
              file=sys.stderr)
        return 2
    league, want_a, want_b = args[0], args[1], args[2]
    wk = int(args[3]) if len(args) > 3 and args[3].isdigit() else None

    c = client_for(league)
    m = c.matchup(wk)
    pool = m.my_lineup + m.their_lineup

    def find(want: str):
        needle = want.strip().lower()
        hits = [p for p in pool if needle in p.name.lower()]
        if len(hits) == 1:
            return hits[0], ""
        if len(hits) > 1:
            return None, f"'{want}' matches {len(hits)}: " + ", ".join(p.name for p in hits)
        return None, (f"'{want}' is not in this week's matchup. compare works on "
                      f"rostered players; free agents need the draft board")

    a, err_a = find(want_a)
    b, err_b = find(want_b)
    for err in (err_a, err_b):
        if err:
            print(err, file=sys.stderr)
    if a is None or b is None:
        return 2

    ids = load_ids()
    api = PffApi()
    state = api.season_state()
    print(head_to_head(a, b, load_usage(api), ids, state.in_season, m.week))
    return 0


def train() -> int:
    """combine train <build|status> [season]

    build     pull a past season's ESPN player-weeks and PFF weekly stat lines
              into SQLite, then resolve any new players onto PFF ids
    status    what is already stored
    baseline  score ESPN and the no-model baselines, which is the bar
    model     fit the residual model and score it on held-out weeks
    backtest  replay a season and test posture-aware lineups against expected
              points, using the same optimizer for both

    Resumable: it skips whatever is already there, so run it again after an
    interruption. A full season is around a hundred requests and some of them
    are slow.
    """
    from . import db
    from .pipeline.crosswalk import directory, load_ids, resolve_by_lookup, resolve_ids, save_ids
    from .pipeline.history import REGULAR_SEASON, coverage, espn_players, pull_espn, pull_pff
    from .pipeline.providers.pff_api import PffApi

    args = sys.argv[2:]
    what = args[0] if args else "status"
    season = int(args[1]) if len(args) > 1 and args[1].isdigit() else config.SEASON - 1

    db.ensure_schema()

    if what == "status":
        with db.connect() as conn:
            cov = coverage(conn, season)
        e = cov["espn"]
        print(f"season {season}")
        print(f"  espn: {e.get('n') or 0} player-weeks, {e.get('wks') or 0}/"
              f"{len(REGULAR_SEASON)} weeks, {e.get('players') or 0} players, "
              f"{e.get('starts') or 0} starts")
        for area, v in sorted(cov["pff"].items()):
            print(f"  pff {area:<10} {v['rows']:>6} rows, {v['weeks']}/"
                  f"{len(REGULAR_SEASON)} weeks")
        if not cov["pff"]:
            print("  pff: nothing stored yet")
        print(f"  crosswalk: {len(load_ids())} espn ids resolved")
        return 0

    if what == "baseline":
        from .pipeline.evaluate import baselines, by_family
        from .pipeline.training import build as build_frame
        with db.connect() as conn:
            frame = build_frame(conn, season)
        if frame.empty:
            print(f"nothing stored for {season}. run: combine train build {season}",
                  file=sys.stderr)
            return 2
        print(f"season {season}: {len(frame)} player-weeks played")
        print("\nBASELINES (the bar a model has to clear)")
        for sc in baselines(frame):
            print("  " + sc.line())
        print("\nESPN BY POSITION FAMILY")
        print(by_family(frame).to_string(index=False))
        return 0

    if what == "backtest":
        from .pipeline.backtest import optimizer_result, posture_modes, posture_sweep, replay
        with db.connect() as conn:
            r = replay(conn, season)
        if not r.n:
            print(f"nothing stored for {season}", file=sys.stderr)
            return 2
        print(f"season {season}: {r.n} team-weeks, real matchups, only my side "
              f"changed.\none standard error on a win-rate delta is "
              f"{r.se:.1f}pp, so read anything smaller as noise.\n")
        print("OPTIMAL SLOT ASSIGNMENT (no forecasting, just arithmetic)")
        print(optimizer_result(r).to_string(index=False,
                                            float_format=lambda v: f"{v:.3f}"))
        print("\nPOSTURE: ceiling when projected to lose, floor when projected "
              "to win,\nagainst the same optimizer run on expected points.")
        print(posture_sweep(r).to_string(index=False,
                                         float_format=lambda v: f"{v:.3f}"))
        print("\nEach ranking applied unconditionally, which isolates the "
              "ranking from the threshold:")
        print(posture_modes(r).to_string(index=False,
                                         float_format=lambda v: f"{v:.3f}"))
        return 0

    if what == "model":
        from .pipeline.evaluate import flip_test
        from .pipeline.model import HOLDOUT_WEEKS, _usable, apply, train_and_score
        from .pipeline.training import build as build_frame
        with db.connect() as conn:
            frame = build_frame(conn, season)
        if frame.empty:
            print(f"nothing stored for {season}. run: combine train build {season}",
                  file=sys.stderr)
            return 2
        table, models = train_and_score(frame)
        print(f"season {season}: ridge on the residual, trained on weeks "
              f"1-13, scored on {min(HOLDOUT_WEEKS)}-{max(HOLDOUT_WEEKS)}\n")
        print(table.to_string(index=False))

        print("\nFLIP TEST: of the close calls where the model disagrees with "
              "ESPN,\nhow often is the model right? Below 50% means it is "
              "noise, not advice.")
        for fam, model in models.items():
            group = frame[frame["family"] == fam]
            hold = _usable(group[group["week"].isin(HOLDOUT_WEEKS)])
            hold = hold.assign(model_pred=apply(model, hold))
            f = flip_test(hold, "model_pred")
            print(f"  {fam:<14} espn {f['base_acc'] * 100:5.1f}%  "
                  f"flips {f['flips']:>5} ({f['flip_rate'] * 100:4.1f}% of pairs)  "
                  f"flip accuracy {f['flip_acc'] * 100:5.1f}% "
                  f"+-{f['flip_se'] * 100:.1f}")

        print("\nA model ships only if it beats ESPN on MAE AND on close-call "
              "ordering,\nand its flips are right more than half the time.")
        return 0

    if what != "build":
        print("usage: combine train <build|status|baseline|model|backtest> [season]",
              file=sys.stderr)
        return 2

    leagues = [s for s, c in config.leagues().items() if c.platform == "espn"]
    print(f"building {season} from {', '.join(leagues)}")
    with db.connect() as conn:
        for league in leagues:
            pull_espn(conn, league, season)
        pull_pff(conn, PffApi(), season)

        # Rosters churn between seasons, so players who appeared in the past
        # season are often missing from a crosswalk built off current rosters.
        people = espn_players(conn, season)
    known = load_ids()
    unknown = [p for p in people if str(p.player_id) not in known]
    print(f"crosswalk: {len(people)} players in {season}, {len(unknown)} unresolved")
    if unknown:
        api = PffApi()
        rows, misses = resolve_ids(unknown, directory(api))
        found, misses = resolve_by_lookup(api, misses)
        rows += found
        stored = save_ids(rows)
        skip = [m for m in misses if m["espn_pos"].upper() in ("D/ST", "DST", "DEF")]
        print(f"  resolved {len(rows)}, {stored} stored, "
              f"{len(misses) - len(skip)} unresolved, {len(skip)} d/st skipped")

    with db.connect() as conn:
        cov = coverage(conn, season)
    print(f"espn player-weeks now: {cov['espn'].get('n')}")
    return 0


def check_scoring() -> int:
    """combine scoring [week] — prove the scoring engine against ESPN itself.

    Scores ESPN's own stat lines under each ESPN league's own rules and compares
    against the points ESPN published. A match means the engine is right, so a
    hand-entered league's numbers are only as wrong as its hand-entered table.
    """
    from .pipeline.scoring import load_table, validate
    from .platforms import client_for

    week = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
    for slug, cfg in config.leagues().items():
        if cfg.platform != "espn":
            continue
        c = client_for(slug)
        r = validate(c, week or c.week)
        print(f"{slug}: {r['matched']}/{r['n']} stat lines within 0.05 "
              f"(worst {r['max_error']:.3f})")
        for name, pos, published, ours in r["worst"][:5]:
            print(f"    {pos:<5} {name[:22]:<23} espn {published:7.2f}  "
                  f"ours {ours:7.2f}  {ours - published:+.2f}")
    for slug, cfg in config.leagues().items():
        if cfg.platform != "manual":
            continue
        table = load_table(config.CONFIG_DIR / f"{slug}_league.toml")
        print(f"\n{slug}: {len(table.rules)} scoring rules loaded from "
              f"config/{slug}_league.toml, idp={table.idp}")
        print("  hand-entered, so it cannot be validated against the platform. "
              "the engine below it is.")
    return 0


def notify() -> int:
    """combine notify [league...] [--force] [--dry-run]

    Run the Sunday check right now. Without --force it behaves exactly as the
    schedule does and stays silent when there is nothing to say, which is the
    normal outcome. Use --force to prove delivery works on a quiet week, and
    --dry-run to see the messages in the terminal without posting.
    """
    import logging

    from .bot import notify as run_notify

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s",
                        force=True)
    args = sys.argv[2:]
    force = "--force" in args
    dry = "--dry-run" in args
    leagues = [a for a in args if not a.startswith("-")]
    unknown = [lg for lg in leagues if lg not in config.leagues()]
    if unknown:
        print(f"unknown league(s): {', '.join(unknown)}. configured: "
              f"{', '.join(config.leagues())}", file=sys.stderr)
        return 2
    return run_notify(leagues or None, force=force, dry_run=dry)


def glossary() -> int:
    """combine glossary — what every token in a role line means."""
    from .pipeline.usage import GLOSSARY, OUTCOME_GLOSSARY

    print("ROLE (PFF usage and efficiency, shown beside the projection and\n"
          "deliberately never blended into it)\n")
    for title, entries in GLOSSARY:
        print(f"  {title}")
        for token, meaning in entries:
            print(f"    {token:<9} {meaning}")
        print()
    print("OUTCOME (the spread around a projection, which ESPN does not give you)\n")
    for token, meaning in OUTCOME_GLOSSARY:
        print(f"    {token:<9} {meaning}")
    print("\n  Context for a close call, not a ranking. Sorting a lineup by "
          "ceiling\n  or floor was backtested and lost at every threshold.")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "week":
        return week()
    if cmd == "pffids":
        return pff_ids()
    if cmd == "startsit":
        return start_sit()
    if cmd == "train":
        return train()
    if cmd == "glossary":
        return glossary()
    if cmd == "scoring":
        return check_scoring()
    if cmd == "notify":
        return notify()
    if cmd == "bot":
        from .bot import main as run_bot

        run_bot(sys.argv[2:])
        return 0
    if cmd == "compare":
        return compare()
    if cmd == "init":
        return init()
    if cmd == "try":
        return try_tools()
    if cmd == "serve":
        from .server import main as serve

        serve()
        return 0
    print("usage: combine [doctor [--live] | init | week <league> [week] | "
          "pffids <league> | startsit <league> [week] | "
          "compare <league> A B | train <build|status|baseline|model> | "
          "try ... | serve]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
