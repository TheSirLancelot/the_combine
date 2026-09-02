# The Combine

Read-only fantasy football co-owner. Live league state plus blended projections,
queried through Claude. It advises, I make every move by hand. Nothing writes
back to ESPN or Yahoo.

Design: `fantasy-copilot-brief.md`. Build order and mechanics: `BUILD_GUIDE.md`.

## Leagues

| slug | platform | league | format |
|------|----------|--------|--------|
| `rcl` | ESPN | The REAL Champions League | IDP keeper, 0.5 PPR, no K/DST |
| `dmwd` | ESPN | Dont Mess With Dexas | full PPR redraft, K + D/ST |
| `work` | Yahoo | work league | pending Yahoo API approval |

Every command takes a slug. One league per call, always.

---

## Draft day

Three commands do everything. Run them from `~/the_combine`.

**The board.** This is the one you live in during the draft.

```bash
uv run combine try board dmwd            # top 20 overall, best available
uv run combine try board dmwd RB 25      # top 25 RBs still on the board
uv run combine try board rcl LB 15       # RCL is IDP, so this matters there
uv run combine try board rcl QB 10
```

It pulls who is still unrostered from ESPN live, so during the draft it updates
as players come off the board. No refresh step, just run it again.

**Positions:** `QB RB WR TE` everywhere, `K` and `D/ST` in dmwd, `LB DL DB` in rcl.

**Reading a row:**

```
  #  POS PLAYER                TM   ESPN    PFF   CONS BYE TIER FLAG
  1  RB  Jahmyr Gibbs          DET 365.7  342.9  354.3   6 T1
  7  WR  Some Guy              MIA 210.4  248.1  229.2   9 T3   PFF+14
 12  RB  Another Guy           NYG 240.1      -  240.1   -  T3   no-pff
```

`ESPN` and `PFF` are each source's season projection **already scored under this
league's rules**, so the numbers are directly comparable. `CONS` is their mean.
`TIER` breaks where the drop to the next player is unusually large, so a tier
edge is a "reach now or wait" boundary.

`FLAG` is where the value is:

- `PFF+14` — PFF ranks him 14 positional spots higher than ESPN. Disagreement is
  the whole reason for having two sources. Worth a look.
- `ESPN+14` — the reverse.
- `no-pff` — PFF has no projection for him at all. On a deep bench guy that is
  usually just a gap. **On a highly ranked player it means something happened to
  him** (suspended, injured, retired) and ESPN has not caught up. Josh Jacobs
  showed up this way. Find out why before you draft him.
- `Q` / `D` / `O` / `IR` — injury status.

**Sanity check the sources.** Run once before the draft, not during.

```bash
uv run combine try crosswalk dmwd
uv run combine try crosswalk rcl
```

Reports how each ESPN player matched a PFF row and prints every non-exact match
to eyeball. Failures land in `data/unmatched_<league>.csv`. `exact` in the high
280s+ with a handful of `nickname` / `fuzzy` is healthy.

**Scoring rules,** if you need to check what a league actually rewards:

```bash
uv run combine try settings rcl
```

---

## Everything else

```bash
uv run combine doctor          # env + league registry
uv run combine doctor --live   # actually hit the platforms
uv run combine try health      # same check, as Claude sees it
uv run combine try leagues     # slugs
uv run combine try roster dmwd # empty until the draft happens
uv run combine try pool rcl RB # ESPN only, no PFF column
uv run combine serve           # MCP server on 127.0.0.1:8787/mcp
```

## Refreshing data

**PFF projections** are a manual export until their API ships. Re-export per
league and drop in as `data/pff/<slug>_projections.csv`, same columns. The
loader picks it up on the next command, no rebuild. Re-export when PFF updates
for injuries or depth chart changes; the file has no freshness check.

**ESPN cookies** expire mid-season and fail as a 401 or an empty league.

```bash
python scripts/refresh_espn_cookies.py   # paste espn_s2 and SWID, rewrites .env
```

## Known gaps

- `get_my_roster` is empty until you draft. `matchup` is not implemented, ESPN's
  box scores 404 in preseason.
- Yahoo (`work`) is waiting on API approval, applied 2026-09-02, 1-2 weeks.
  It shows as SKIP in doctor, which is expected, not broken.
- The board blends already-scored point totals rather than rescoring PFF's stat
  lines through your league rules. Deliberate shortcut. The stat lines are
  parsed and kept, so fixing it later changes the blend, not the ingest.

## Rules

Read-only. No write or transaction methods, ever, not even unused ones.
Secrets in `.env` only. PFF data stays in `data/`, gitignored, never redistributed.
Tool outputs stay small: every list capped server-side, one league per call.
