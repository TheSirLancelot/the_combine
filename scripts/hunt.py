#!/usr/bin/env python3
"""Try every endpoint that might carry live draft picks. Prints what has data."""
import json, os, sys
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from combine.config import SEASON, get_league  # noqa: E402

cfg = get_league(sys.argv[1] if len(sys.argv) > 1 else "rcl")
CK = {"espn_s2": os.environ["ESPN_S2"], "SWID": os.environ["ESPN_SWID"]}
BASE = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
        f"/segments/0/leagues/{cfg.league_id}")

def show(label, url, **kw):
    try:
        r = requests.get(url, cookies=CK, timeout=12, **kw)
        if r.status_code != 200:
            print(f"{label:<38} HTTP {r.status_code}")
            return
        body = r.text
        d = r.json()
        # crude but fast: how many player ids and how big
        print(f"{label:<38} 200  {len(body)//1024}kb  keys={list(d)[:6]}")
        if "topics" in d:
            t = d.get("topics") or []
            print(f"{'':<38}      topics={len(t)}")
            if t:
                print(f"{'':<38}      {json.dumps(t[-1])[:200]}")
        if "teams" in d:
            n = sum(len((x.get("roster") or {}).get("entries") or []) for x in d["teams"])
            print(f"{'':<38}      rostered={n}")
    except Exception as exc:
        print(f"{label:<38} {type(exc).__name__}: {exc}")

show("communication/kona_league_comm",
     BASE + "/communication/", params={"view": "kona_league_communication"})
show("mRoster scoringPeriodId=0", BASE, params={"view": "mRoster", "scoringPeriodId": 0})
show("mRoster scoringPeriodId=1", BASE, params={"view": "mRoster", "scoringPeriodId": 1})
show("mTransactions2", BASE, params={"view": "mTransactions2"})
show("mDraftDetail+mTeam+mRoster", BASE,
     params=[("view", "mDraftDetail"), ("view", "mTeam"), ("view", "mRoster")])
show("allon (kona_player_info off)", BASE, params={"view": "allon"})
