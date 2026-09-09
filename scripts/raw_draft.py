#!/usr/bin/env python3
"""Hit ESPN's draft endpoints directly, both hosts, and report pick counts.

  uv run python scripts/raw_draft.py rcl
"""
import json, os, sys
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from combine.config import SEASON, get_league  # noqa: E402

cfg = get_league(sys.argv[1] if len(sys.argv) > 1 else "rcl")
cookies = {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]}

HOSTS = [
    "https://lm-api-reads.fantasy.espn.com",   # what espn-api uses
    "https://fantasy.espn.com",                # what the draft room uses
]
VIEWS = ["mDraftDetail", "mRoster", "mTeam"]

for host in HOSTS:
    url = f"{host}/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{cfg.league_id}"
    for view in VIEWS:
        try:
            r = requests.get(url, params={"view": view}, cookies=cookies, timeout=15)
            if r.status_code != 200:
                print(f"{host.split('//')[1][:22]:<24} {view:<14} HTTP {r.status_code}")
                continue
            d = r.json()
            if view == "mDraftDetail":
                dd = d.get("draftDetail") or {}
                picks = dd.get("picks") or []
                print(f"{host.split('//')[1][:22]:<24} {view:<14} "
                      f"inProgress={dd.get('inProgress')} drafted={dd.get('drafted')} "
                      f"picks={len(picks)}")
                if picks:
                    print("      sample:", json.dumps(picks[-1])[:220])
            else:
                teams = d.get("teams") or []
                n = sum(len((t.get("roster") or {}).get("entries") or []) for t in teams)
                print(f"{host.split('//')[1][:22]:<24} {view:<14} teams={len(teams)} rostered={n}")
        except Exception as exc:
            print(f"{host.split('//')[1][:22]:<24} {view:<14} {type(exc).__name__}: {exc}")
