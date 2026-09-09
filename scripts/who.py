#!/usr/bin/env python3
import os, sys, collections
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from combine.config import SEASON, get_league  # noqa: E402

cfg = get_league(sys.argv[1] if len(sys.argv) > 1 else "rcl")
r = requests.get(
    f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
    f"/segments/0/leagues/{cfg.league_id}",
    params={"view": "mDraftDetail"},
    cookies={"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]},
    timeout=15)
picks = [p for p in (r.json().get("draftDetail") or {}).get("picks", [])
         if (p.get("playerId") or -1) > 0]
print(f"configured team_id: {cfg.team_id!r}")
print("teamId counts:", dict(collections.Counter(p.get("teamId") for p in picks)))
print("keepers:", sum(1 for p in picks if p.get("keeper")),
      " real picks:", sum(1 for p in picks if not p.get("keeper")))
print("first 5:", [(p["overallPickNumber"], p.get("teamId"), p.get("keeper"))
                   for p in picks[:5]])
