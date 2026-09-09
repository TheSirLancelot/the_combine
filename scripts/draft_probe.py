#!/usr/bin/env python3
"""Is ESPN telling us about live draft picks? Run mid-draft.

  uv run python scripts/draft_probe.py rcl
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from combine.config import get_league  # noqa: E402
from combine.platforms import client_for  # noqa: E402

slug = sys.argv[1] if len(sys.argv) > 1 else "rcl"
cfg = get_league(slug)
c = client_for(slug)
lg = c.league

print(f"league {slug}  current_week {lg.current_week}")

# 1. does the draft endpoint know about picks?
try:
    picks = lg.draft
    print(f"draft picks seen: {len(picks)}")
    for p in picks[-5:]:
        print(f"   R{p.round_num}.{p.round_pick}  {p.playerName}")
except Exception as exc:
    print(f"draft endpoint: {type(exc).__name__}: {exc}")

# 2. are picks landing on rosters?
total = 0
for t in lg.teams:
    total += len(t.roster)
print(f"players on all rosters: {total}")
mine = next((t for t in lg.teams if str(t.team_id) == str(cfg.team_id)), None)
if mine:
    print(f"my roster ({mine.team_name}): {[p.name for p in mine.roster]}")

# 3. is the free agent pool shrinking?
fa = lg.free_agents(size=200)
print(f"free agents returned: {len(fa)}")
print(f"top 5 available: {[p.name for p in fa[:5]]}")
