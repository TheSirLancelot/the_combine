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
  #  POS PLAYER                TM   ESPN    PFF    AVG    VOR   ADP  VAL BYE TIER  BUZZ FLAG
  1  RB  Jahmyr Gibbs          DET 365.7  342.9  354.3  185.2   1.3   +0   6 T1      +2
  7  WR  Some Guy              MIA 210.4  248.1  229.2   96.4  41.2  +34   9 T3   SPLIT VALUE+34
 14  RB  Another Guy           NYG 240.1      -  240.1   71.0  64.1  +50  11 T3      -1 NEWS!
```

| col | what it is | how to use it |
|-----|-----------|---------------|
| `#` | Overall rank by `VOR` across the whole pool. Does not shift when you filter. | Compare across positions. |
| `POS` | Position plus his rank at it, e.g. `RB4` = fourth best RB available. Also whole-pool. | Compare within a position. |
| `PLAYER` | ESPN's name, truncated at 21 chars. | The PFF row it matched may be spelled differently; `try crosswalk` shows pairs. |
| `TM` | NFL team, ESPN's spelling. | PFF writes some differently (`HST`, `ARZ`, `LA`). Handled internally. |
| `ESPN` | ESPN's projected season points, **scored under this league's rules**. | One opinion. Do not read alone. |
| `PFF` | PFF's projected season points, also scored under this league's rules. Dash means no match. | Second opinion. Two sources agreeing is weak evidence; disagreeing is the useful part. |
| `AVG` | Plain mean of the projection sources (`ESPN`, `PFF`, and whatever gets added later). Falls back to whichever exist. | Raw projected points. Comparable *within* a position, misleading across them. Does **not** set the order. |
| `VOR` | `AVG` minus replacement level at his position, where replacement is the last player who starts somewhere in a 12-team league. | **This sets the order.** It is what makes a QB and an RB comparable. See below. |
| `ADP` | PFF's average draft position, from the rankings export **matching this league's scoring format**. Dash means unknown. | Where the room takes him. Also tells you roughly whether he survives to your next pick. |
| `VAL` | `ADP` minus his overall `VOR` rank. Positive = the room takes him later than the numbers say he is worth. | **Who** is underpriced, never **when** to take him. A big `+` often means you can wait, see below. |
| `BYE` | Bye week. | Late rounds, avoid stacking your starters on one week. |
| `TIER` | Tier **within his position**, computed by me, not by PFF. Breaks where the drop in `VOR` to the next player at that position is more than 1.6x the typical drop. | A tier edge is the "take him now or wait a round" line. Within a tier, take the best `VAL`. Whole-pool, so it does not change when you filter. |
| `BUZZ` | Net analyst sentiment from the opinion lists, or `SPLIT` when they contradict each other. Blank = nobody mentioned him. | Never in the blend. `SPLIT` is the interesting one; `try notes` gives the detail. |
| `FLAG` | The single most important thing about the row. See below. | Read this before the numbers. |

**Why `VOR` and not `AVG`.** Raw points do not decide when a player is
drafted. A QB projected for 300 when the 12th best QB gets 280 gives you 20
points of edge over what you could have had anyway. An RB projected for 250
when RB24 gets 130 gives you 120. The RB goes far earlier despite scoring
fewer points.

This bit hard in RCL, where the pool is full of linebackers projected for 230
and quarterbacks over 300 that nobody drafts early. Ordering on `AVG` pushed
every running back down the board and made **every** `VAL` negative, because
`ADP` is a draft-order number and `AVG` rank is not. Replacement level is
computed from your league's own slot counts times 12 teams, with flex slots
split across the positions eligible for them.

Practical upshot: read `AVG` to compare two RBs, read `VOR` to compare an RB
against a QB. If `VAL` ever goes systematically negative again, that is the
symptom of this same class of bug.

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

**What `VAL` does not mean.** It says nothing about whether a player is good,
only whether he is cheap. A 180th-ranked player with `VAL+40` is still the
180th best player. Every rank on the board (`#`, the number in `POS`, and the
one inside `VAL`) is computed against the whole 250-player pool, so filtering
by position never changes them.

**`FLAG` values,** in the priority order the code emits them (a row shows one):

| flag | means | do |
|------|-------|-----|
| `OUT?` | Ranked with a real ADP but projected **zero** points. Something happened and the market has not caught up. | Look him up before spending a pick. Josh Jacobs presents this way. |
| `no-pff` | No PFF projection at all. `AVG` is ESPN alone, undiluted. | Deep bench guy, usually a gap. Highly ranked, treat like `OUT?`. |
| `NEWS!` | Reporting the projections have not absorbed: out, IR, week-to-week, suspension risk. | `try notes` for the detail, source and date. Re-check these the morning of the draft. |
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
uv run combine try plan rcl 1          # league, pick currently on the clock
uv run combine try plan rcl 24
uv run combine try plan rcl 1 RB       # one position
uv run combine try plan dmwd 15 20     # second number = how many rows
uv run combine try plan dmwd 15 --slot=9   # override the configured slot
```

```
GONE before pick 24 -- take one of these now
     #  POS   PLAYER                TM     VOR   ADP  VAL  FLAG
    #1  RB1   Jahmyr Gibbs          DET  185.2   1.4   +0
    #3  RB2   Bijan Robinson        ATL  169.6   2.0   -1
```

`#1` is overall rank, `RB1` is his rank at the position. Both are computed
against the whole pool, so they do not shift when you filter.

Your draft slot comes from `<SLUG>_DRAFT_POS` in `.env` (`RCL_DRAFT_POS=1`),
so the only thing you type mid-draft is the pick on the clock. `combine doctor`
prints the configured slot per league; `--slot=N` overrides it for one run.

Splits the best available into three groups against your next snake pick:

- **GONE** — ADP says he is off the board before your next turn. Your real
  choices. Take the best `VAL` here.
- **COIN FLIP** — within 8 picks of your next turn either way. ADP is an
  average, not a deadline, so this bucket exists on purpose.
- **STILL THERE** — he lasts. Even a huge `VAL` here can wait; spend the pick
  on someone from GONE and come back for him.
- **NO ADP** — timing unknown. In RCL that is every defender.

`TIER` shows on the plan rows too, and it is the second thing to read after
the bucket. If everyone left in GONE at your position is the same tier as
several players in STILL THERE, the position is not actually scarce and you
should spend the pick elsewhere.

Within each group, rows are ordered by `VOR`, best player first, **not** by
`VAL`. Everyone in GONE is someone you cannot wait on, so the cost of waiting
is already zero for all of them and the only question left is who is best.
`VAL` chooses between groups; `VOR` orders within them. Use `VAL` as a
tiebreaker when two players are close in `VOR`, and ignore it when they are
not.

With no position filter it also prints a **scarcity** table: how many players
at each position fall into gone / flip / left. That is the run-detection view.
If RB shows 9 gone and 3 left while WR shows 6 gone and 15 left, spend this
pick on a back and take receivers at your next turn. It reads across the whole
pool even when you filter, since comparing positions is the point. In RCL
every defender lands in `noadp`, so it says nothing about IDP scarcity.

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
uv run combine try notes dmwd Kittle  # news + analyst detail on one player
uv run combine try roster dmwd # empty until the draft happens
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

**Analyst opinion** lives in `data/opinion/`, any number of CSVs with columns
`player,list,polarity` where polarity is `1` / `0` / `-1`. Currently ESPN's
Ultimate Cheat Sheet (nine analyst lists, hand-transcribed from a PDF that
does not parse) and NFL.com's late-round sleepers. Adding a source is a file,
not a code change. Feeds `BUZZ`, never the blend.

**Player news** lives in `data/news/`, columns
`player,severity,status,note,source,as_of`. Severity `high` raises the `NEWS!`
flag, `medium` and `low` show only in `try notes`. Every row carries a source
and a date because this is hand-curated, not a feed. **Re-check the high rows
before each draft**; hamstrings move fast.

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
