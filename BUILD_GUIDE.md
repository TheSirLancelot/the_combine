# The Combine — build guide

Companion to `fantasy-copilot-brief.md` (the architecture) and `README.md` (how
to use it). This is what got built, where it diverged from the original plan
and why, and what is still owed.

Last updated 2026-09-09, drafts done, week 1 lineup view built, season starting.

---

## Status

**Drafts are done.** All three leagues drafted 2026-09-06. The draft toolchain
(board, VORP, tiers, ADP, timing, targets by pick) worked and is committed. It
is now off-season code until next August.

**The season is what matters now.** Roster and matchup were deliberately
skipped pre-draft because ESPN returns an empty roster and 404s on box scores
in preseason. Both are now built, see item 1 below.

**PFF API is live and we have a working key.** It is NOT what the original
brief assumed, see below.

**Still not built.** Tunnel, Cloudflare WAF rule, connector registration.
Nothing is reachable from a phone. Everything runs from `uv run combine ...`
or the Streamlit app.

**Blocked.** Yahoo. Fantasy Sports API access is behind a manual review,
applied 2026-09-02, quoted 1-2 weeks. The `work` league shows SKIP in doctor,
which is expected rather than broken.

## What changed from the original plan

The brief's build order was: MCP servers, then tunnel and connector, then the
crosswalk, then the blend, then PFF when its API ships. Reality reordered most
of that, and the reasons are worth keeping.

**The draft moved to the front.** All three drafts landed on 2026-09-06, four
days after the repo existed. Everything got reprioritised around being useful
on that day. Roster and matchup tools were deliberately skipped, because ESPN
returns an empty roster and 404s on box scores in preseason, so they could be
written but not verified. Draft prep works entirely off the free-agent pool,
which preseason is the whole draftable player universe.

**PFF arrived early, as CSVs.** It was Phase 4, gated on an API that has not
shipped. Instead the exports came in by hand: two per-league projection files
with full 61-column stat lines, plus rankings exports carrying ADP. That turned
the blend from a one-source stub into a real two-source system before the
draft. The provider model absorbed it without changes, which is the first
actual evidence that the N-source design works.

**The PFF API is not a projections source.** The brief assumed the PFF API
would slot in as another projection provider. It does not. Verified against
their OpenAPI spec and live calls on 2026-09-09: 70 endpoints of grades and
charted stats, titled "Premium Stats Pro CLI API". There is no projection, no
ranking and no ADP endpoint anywhere in it. Projections and ADP still come
from hand exports.

What it is good for is better than a redundant projection:
  * actuals, which is what the `actual` table has always wanted
  * real inputs for our own model, currently a stub: routes, route rate, slot
    rate, aDOT, contested catch, drops, EPA, pressure, snap counts
  * defense, which matters for RCL where IDP projections are thin

**We never wrote `scoring.py`.** Both ESPN and PFF hand back projections
already scored under each league's own rules. Same player, same raw stat line,
different point totals per league. So the board blends finished totals rather
than rescoring stat lines. This is a documented shortcut, not an oversight. The
stat lines are parsed and kept, so doing it properly later changes the blend
and not the ingest.

**We added value over replacement, which was not in the plan at all.** `VAL`
compares ADP, a draft-order number, against our rank. Ranking on raw points
made every `VAL` negative, because RCL's pool is full of 230-point linebackers
and 300-point quarterbacks that nobody drafts early. Replacement level comes
from each league's own slot counts times team count. This is the single most
important correctness fix in the repo.

**We added two layers the brief never imagined.** `data/opinion/` holds analyst
lists (ESPN's cheat sheet, NFL.com sleepers) and `data/news/` holds injury and
legal status. Both are hand-curated, both sit beside the numbers, and neither
enters the blend. Opinion has no scale and no scoring format; folding it into
`AVG` would corrupt a number that currently means something. News is the layer
that says the projection snapshot is stale, which before a draft is often the
most valuable information in the system. Josh Jacobs was the worked example:
arrested, zeroed by PFF, still fully projected by ESPN.

**The stat vocabulary came from ESPN, not from us.** The original schema
invented names like `pass_yd`. The real answer was in
`settings.scoring_format`: a numeric ESPN stat id with an abbreviation and a
point value, different per league. That was found by writing a probe script
against the live API rather than guessing, which is a pattern worth repeating.

**SQLite is created and unused.** `combine init` builds the schema and nothing
reads or writes it. Every command runs live: hit ESPN, parse the CSVs, build
the board in memory, print. For a draft that is correct, since staleness is the
enemy and the whole thing takes a second or two. It becomes wrong the moment we
want a projected-versus-actual accuracy log, or a Discord bot, or any consumer
that is not a person waiting on a prompt.

---

## What exists

```
src/combine/
  config.py            league registry, env, draft slots
  db.py, schema.sql    created, not yet used by anything
  format.py            tiering, compact row rendering
  cli.py               combine doctor | init | try ... | serve
  server.py            7 MCP tools, bearer auth, loopback
  platforms/
    espn.py            real. written against probe output, not docs
    yahoo.py           ping only, blocked on API approval
  pipeline/
    board.py           merge sources, VORP, tiers, flags
    vorp.py            replacement level from league slots
    crosswalk.py       ESPN <-> PFF name matching, conservative
    draftplan.py       snake picks, gone / coin flip / still there
    needs.py           roster-aware slot gaps and bye pileups
    providers/
      pff_csv.py       per-league projections, stat lines
      pff_rankings.py  per-league ADP + analyst ranks, plus IDP
      opinion.py       any CSV in data/opinion/
      news.py          any CSV in data/news/
      espn_proj.py     stub
      own_model.py     stub
    scoring.py         stub, see "we never wrote scoring.py"
    blend.py, run.py   stubs
scripts/
  probe_espn.py            dump real API shapes before writing adapters
  refresh_espn_cookies.py  30-second recovery when cookies die
  yahoo_login.py           one-time OAuth, blocked
```

---

## What is still owed, in order

**1. Roster and matchup. DONE 2026-09-09**, branch `season/weekly-lineup`.
They turned out to be one thing, not two. `box_scores()` works from week 1 and
is the *only* place weekly projections live: players off `team.roster` come
back with `projected_points=None` and `points=None`. So the in-season path
reads box scores and the draft path keeps reading the roster, and they use
different objects (`WeeklyPlayer` vs `PlayerState`) rather than one object with
more fields filled in.

`WeeklyPlayer` carries `eligible_slots`, which is the field start/sit needs:
it is what makes a bench-over-starter swap legal or not. `pipeline/lineup.py`
splits starters from bench, flags problem starters, and lists bench players
outprojecting a starter whose slot they can fill. That last one is deliberately
*not* the optimizer: it is greedy, one for one, and runs on ESPN's weekly
projection alone, because that is the only weekly number the system has until
the PFF client lands. `tests/test_lineup.py` covers the slot logic.

Two things ESPN does not give us here. `pro_opponent` comes back as the string
`"None"` and `pro_pos_rank` is 0 on box players, so **opponent and defensive
matchup need their own source**, and that probe belongs before start/sit rather
than inside it. And no games have been played yet, so every `actual` is 0 and
item 4 below has nothing to log until week 1 finishes.

**2. PFF API client.** Auth is a bearer token, `PFF_API_KEY=ak_...` in `.env`,
base URL `https://api.pff.com`. Two endpoint families that behave differently:
  * `/v1/facet/<area>/<report>` returns every player at once, no id needed.
    `?league=nfl&season=2025` gave 792 rows of receiving with player_id, name,
    team, position, grades and charted stats. This is the one to build on.
  * `/v1/player/<area>/<report>` needs an explicit `player_id`.
  * `/v1/players?name=` is name lookup, and returns PFF's `player_id`. That id
    is the anchor for extending the crosswalk to a third source.
Probe first, adapter second, same as ESPN. Do not build against the docs.

**The season parameter is a trap.** Probed 2026-09-09:
`facet/receiving/summary?season=2026` returns 602 rows, but they are PRESEASON
only, the target leaders are camp bodies at ~24 targets.
`season=2026&week=1` returns 0 rows. `season=2025` returns 792 for the season
and 251 for `week=1`, so the `week` parameter does work. Meaning: week 1
start/sit gets its PFF signal from 2025 full-season grades as a prior, real
2026 data only exists from week 2 on, and the client needs an explicit
season/week policy. Defaulting to the current season silently serves noise in
September and real data in October, which is the worst possible failure mode.

Extend the crosswalk to PFF ids as part of this step, not after. ESPN and PFF
are currently matched by name at ~97%, and the API hands us a stable id. Doing
it later means unpicking start/sit code that was built on name matching.

**3. Start/sit and player comparison.** The reason for all of this. Compare two
players on blended projection plus the PFF usage and efficiency stats, scoped
to a league and a week. Needs 1 and 2 first.

**4. Persist to SQLite.** Still created and unused. The `actual` table wants
weekly writes from week 1 onward, and that is the only path to weighting the
blend by measured accuracy instead of by opinion.

**5. Remote access.** Tunnel route, WAF rule pinned to Anthropic's egress range
`160.79.104.0/21`, connector registered with a static bearer header. The server
enforces its own token independently of Cloudflare. Do not put a Cloudflare
Access policy on the hostname, it bounces Anthropic with a login redirect and
fails with a useless error.

**6. Yahoo, when approved.** Key and secret into `.env`, run
`scripts/yahoo_login.py`, write the adapter from probe output. Redirect URI must
be `https://localhost:8000`.

**7. Rescore properly.** Write `scoring.py`, apply each league's stat-id scoring
to PFF's raw stat lines, compare against PFF's own `fantasyPoints`. Match means
the shortcut was safe; mismatch means someone has a scoring bug.

**8. Live draft reader.** For next August. ESPN's league API does not expose an
in-progress draft; picks ride a comet channel at `fantasydraft.espn.com`. See
the draft-day notes in project memory.

**9. Discord bot.** Unchanged from the brief.

## Known soft spots

These are all documented in the README where a user would hit them, and listed
here so they do not get rediscovered as surprises.

**Tiering is mine and unvalidated.** Tiers break where the drop in VORP exceeds
1.6x the median drop. That constant was picked, not derived. PFF's own tiers
were examined and are not reproducible from any field in their export: points
are non-monotonic inside their tiers and rise across five of twelve boundaries,
so their tiers are analyst-drawn on an analyst-adjusted board. There is no
formula to copy.

**RCL's ADP is soft.** It is a keeper league, so 24 players are gone in ways
public ADP cannot know, and PFF publishes no IDP draft position at all, so
every defender has no ADP and no VAL.

Keepers themselves are fine: verified 2026-09-03 that ESPN has already
rostered them and they do not appear in the free-agent pool. That also means
the pool reflects roster state rather than a static player list, which is
indirect evidence it will shed players as they are drafted. Still worth
confirming live after the first couple of picks.

**Everything hand-curated is a snapshot.** The PFF exports, the opinion lists
and the news file have no freshness check. An August export will serve October
numbers without complaint. News rows carry a source and a date for exactly this
reason.

**The crosswalk refuses to guess.** Match rate is around 97% with the remainder
reported, never silently matched. Unmatched players are usually genuinely
absent from PFF's data rather than a matching failure, and that absence is
itself a signal.

**ESPN cookies will die mid-season.** `scripts/refresh_espn_cookies.py` makes
recovery a 30-second paste. Run `doctor --live` before anything that matters.
