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
        print(f"          {slug:10s} {cfg.platform:6s} id={cfg.league_id}")

    if "--live" in sys.argv:
        from .platforms import client_for

        print("live      probing platforms")
        for slug in lg:
            try:
                print(f"          {slug:10s} OK  {client_for(slug).ping()}")
            except Exception as exc:
                print(f"          {slug:10s} FAIL {type(exc).__name__}: {exc}")
    return 1 if missing else 0


def init() -> int:
    db.ensure_schema()
    with db.connect() as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"schema ready at {config.DB_PATH}")
    print("tables: " + ", ".join(t for t in tables if not t.startswith("sqlite_")))
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        return doctor()
    if cmd == "init":
        return init()
    if cmd == "serve":
        from .server import main as serve

        serve()
        return 0
    print("usage: combine [doctor [--live] | init | serve]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
