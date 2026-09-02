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
  #  POS PLAYER                TM   ESPN    PFF   CONS   ADP  VAL BYE TIER FLAG
  1  RB  Jahmyr Gibbs          DET 365.7  342.9  354.3   1.3   +0   6 T1
  7  WR  Some Guy              MIA 210.4  248.1  229.2  41.2  +34   9 T3   VALUE+34
 14  RB  Another Guy           NYG 240.1      -  240.1  64.1  +50  11 T3   OUT?
```

| col | what it is | how to use it |
|-----|-----------|---------------|
| `#` | Row number in what you asked for. Filter by position and it renumbers. **Not** an overall rank. | Ignore it. It is a line number. |
| `POS` | ESPN's position for that player. | RCL uses `LB DL DB DP` slots, DMWD uses `K D/ST`. |
| `PLAYER` | ESPN's name, truncated at 21 chars. | The PFF row it matched may be spelled differently; `try crosswalk` shows pairs. |
| `TM` | NFL team, ESPN's spelling. | PFF writes some differently (`HST`, `ARZ`, `LA`). Handled internally. |
| `ESPN` | ESPN's projected season points, **scored under this league's rules**. | One opinion. Do not read alone. |
| `PFF` | PFF's projected season points, also scored under this league's rules. Dash means no match. | Second opinion. Two sources agreeing is weak evidence; disagreeing is the useful part. |
| `CONS` | Mean of `ESPN` and `PFF`. Falls back to ESPN alone when PFF is missing. | This sets the sort order. It is the board's opinion of who is best. |
| `ADP` | PFF's average draft position, from the rankings export **matching this league's scoring format**. Dash means unknown. | Where the room takes him. Also tells you roughly whether he survives to your next pick. |
| `VAL` | `ADP` minus overall consensus rank. Positive = the room takes him later than the numbers say he is worth. | **Who** is underpriced, never **when** to take him. A big `+` often means you can wait, see below. |
| `BYE` | Bye week. | Late rounds, avoid stacking your starters on one week. |
| `TIER` | Groups players where the drop to the next is unusually steep. | A tier edge is the "take him now or wait a round" line. Within a tier, take the best `VAL`. |
| `FLAG` | The single most important thing about the row. See below. | Read this before the numbers. |

**A high VAL is not "take him now."** It is closer to the opposite. `VAL+34`
on the 7th best available player means the room usually takes him around pick
41. That says two things at once: he is underrated, *and* he will probably
still be sitting there at your next pick. Taking him early wastes the gap.

The number that decides **when** is `ADP` compared to **your next pick**, not
the size of `VAL`. Pick 12th with your next turn at 36? A player with ADP 41
is very likely still there at 36, so spend pick 12 on someone who will not be.
A player with ADP 22 is gone, so it is now or never.

The rule: **`ADP` decides when, `VAL` decides who.** Of the players who will
not survive to your next turn, take the biggest `VAL`. That is the pick where
you paid less than the player is worth and genuinely could not have waited.
`combine try plan` does this comparison for you.

**Two things `#` and `VAL` do not mean.** `#` renumbers per query, so the
5th row of `board rcl WR` is not the 5th best player available. `VAL` is
computed against the whole 250-player pool regardless of your filter, so it
stays comparable across positions. And `VAL` says nothing about whether a
player is good, only whether he is cheap. A 180th-ranked player with `VAL+40`
is still the 180th best player.

**`FLAG` values,** in the priority order the code emits them (a row shows one):

| flag | means | do |
|------|-------|-----|
| `OUT?` | Ranked with a real ADP but projected **zero** points. Something happened and the market has not caught up. | Look him up before spending a pick. Josh Jacobs presents this way. |
| `no-pff` | No PFF projection at all. `CONS` is ESPN alone, undiluted. | Deep bench guy, usually a gap. Highly ranked, treat like `OUT?`. |
| `VALUE+n` / `REACH-n` | ADP disagrees with the projections by 12+ spots. | `VALUE` = underpriced, but check `plan` before taking him; he may last. `REACH` = the room likes him more than the numbers do. |
| `PFF+n` / `ESPN+n` | The two projection sources disagree by 10+ positional spots on a normally-priced player. | Coin flip the numbers can't settle. Use your own read. |
| `PFFRK+n` | PFF's analysts rank him n spots away from where PFF's own projections put him. Humans overriding the model. | The only market-ish signal on the IDP side, where no ADP exists. |
| `Q` `D` `O` `IR` | Injury status, appended after any of the above. | |

**Where columns go blank, and why.** `ADP` and `VAL` are always dashes for
RCL defenders, because PFF publishes no IDP draft position anywhere. RCL is
also a keeper league, so two players per team are gone in ways public ADP
cannot know; treat RCL `VAL` as a hint, not a number. The half-PPR export
also drops to null past about ADP 130, so RCL shows dashes earlier down the
board than DMWD does.


**When to take him.** The board says who is worth what. This says who survives
to your next pick.

```bash
uv run combine try plan rcl 1 1       # league, your draft slot, pick on the clock
uv run combine try plan rcl 1 24
uv run combine try plan dmwd 7 15
```

Splits the best available into three groups against your next snake pick:

- **GONE** — ADP says he is off the board before your next turn. Your real
  choices. Take the best `VAL` here.
- **COIN FLIP** — within 8 picks of your next turn either way. ADP is an
  average, not a deadline, so this bucket exists on purpose.
- **STILL THERE** — he lasts. Even a huge `VAL` here can wait; spend the pick
  on someone from GONE and come back for him.
- **NO ADP** — timing unknown. In RCL that is every defender.

Team count and round count come from ESPN. You only supply the slot. Picking
first in a 12-team league your picks are 1, 24, 25, 48, 49, so 22 players go
off the board between your first and second turn; almost nothing you want at
pick 1 survives it. Picks 24 and 25 back to back are the one place you can be
greedy, since nothing moves in between.

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
uv run combine try plan rcl 1 1 # league, slot, pick on the clock
uv run combine try roster dmwd # empty until the draft happens
uv run combine try pool rcl RB # ESPN only, no PFF column
uv run combine serve           # MCP server on 127.0.0.1:8787/mcp
```

## Refreshing data

**PFF data** is a manual export until their API ships. Three files, all in
`data/pff/`, all picked up on the next command with no rebuild step:

| file | export to pull | gives |
|------|----------------|-------|
| `rcl_projections.csv` | projections, RCL scoring synced | stat lines + RCL-scored points |
| `dmwd_projections.csv` | projections, DMWD scoring synced | stat lines + DMWD-scored points |
| `rcl_rankings.csv` | draft rankings, **half PPR** | ADP, rank, bye |
| `rcl_rankings_idp.csv` | draft rankings, **IDP** | rank, bye (no ADP exists) |
| `dmwd_rankings.csv` | draft rankings, **full PPR** | ADP, rank, bye |

**Pull the rankings export matching each league's scoring.** ADP is
format-specific and the gap is real: Josh Jacobs is ADP 64.1 in the full-PPR
export and 39.9 in the half-PPR one. Using the wrong file skews every VAL on
that board.

Every rankings export ships with a title line above the header. Strip it, the
header must be line 1. Re-export when PFF updates for injuries or depth chart
moves; nothing has a freshness check, so an August file will serve October
numbers without complaint.

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
- The rankings exports have a Projected Points column that is deliberately
  ignored. It uses PFF's default scoring, not either league's, and mixing it
  into the consensus would quietly corrupt it. Only ADP and rank are used.

## Rules

Read-only. No write or transaction methods, ever, not even unused ones.
Secrets in `.env` only. PFF data stays in `data/`, gitignored, never redistributed.
Tool outputs stay small: every list capped server-side, one league per call.
