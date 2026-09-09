#!/usr/bin/env python3
"""Does draft_picks() see anything, and do the ids match the pool?"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from combine.platforms import client_for  # noqa: E402

c = client_for(sys.argv[1] if len(sys.argv) > 1 else "rcl")
taken, mine = c.draft_picks()
print(f"taken={len(taken)} mine={len(mine)}")
print("sample taken ids:", list(taken)[:5])
pool = c.free_agents(limit=250)
print("sample pool ids:", [p.player_id for p in pool[:5]])
overlap = {p.player_id for p in pool} & taken
print(f"pool players that are already drafted: {len(overlap)}")
