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

Opponent needed a second source and now has one. The box score reports
opponent pro-team id 0, which espn-api renders as the string `"None"`, so the
first cut of this view was opponent-blind. `proTeamSchedules_wl` is the answer:
one season-level request covering all 32 teams and every week, with home/away
and kickoff timestamp. `EspnClient.pro_schedule(week)` wraps it and caches per
week. Two things fell out of it for free: bye is now derived from a team having
no game that week rather than from ESPN's own flag, and kickoff time gives us
`locked`, so the swap list stops suggesting moves for players whose game has
already started. Defensive matchup strength is still owed and belongs to PFF's
team defense facets.

No games have been played yet, so every `actual` is 0 and item 4 below has
nothing to log until week 1 finishes.

**2. PFF API client and PFF ids. DONE 2026-09-09.**
`pipeline/providers/pff_api.py`, plus the crosswalk extension below. Auth is a
bearer token, `PFF_API_KEY=ak_...` in `.env`, base `https://api.pff.com`.

Four things the probe taught us that the docs do not say:

  * **The envelope key is not mechanical, and neither is the shape.**
    receiving/summary is a list under `receiving_summary`, but
    defense/coverage_matchup answers under `receiving_coverage_stats` as a
    dict of three lists: `defenders` (1003), `receivers` (792) and `versus`
    (14099 receiver-against-defender rows keyed by `player_id` and
    `coverage_player_id`). So the client takes the first value in the payload
    rather than constructing the key, and flat and grouped reports have
    separate accessors. `versus` is the WR-against-CB table start/sit will
    want.
  * **Season is a trap, and `/v1/leagues` is the way out.** A season that has
    not kicked off still returns rows and they are preseason: `season=2026`
    gave 602 receiving rows led by camp bodies at 24 targets, while
    `season=2026&week=1` gave zero. Nothing in a row says which it is.
    `/v1/leagues` reports `default_season` and `default_week`, where a week
    below 1 is preseason (Hall of Fame is -1, preseason week 1 is -2). So
    `season_state().stats_season` is last season until kickoff, and the client
    says which season it is using rather than silently serving noise in
    September and real data in October.
  * **Calls vary from 1s to 22s.** passing/summary is 142 rows in 2s,
    defense/summary is 1456 rows and 1.7MB in 15s, coverage_matchup is 4MB in
    22s. The client caches to `data/pff_api/` with a 12h TTL. That is not an
    optimisation, it is what makes the Streamlit page usable.
  * **`/v1/players?name=` is a substring search.** "Josh Allen" also returns
    Josh Hines-Allen, so callers must filter rather than take the first hit.

**The crosswalk now resolves ESPN ids onto PFF ids**, stored in
`data/crosswalk_pff_ids.csv` and rebuilt with `combine pffids <league>`. Both
leagues resolve at 100%, 559 players stored. Three passes, each more
conservative than the last:

  1. A directory built from five facet reports (passing, rushing, receiving,
     defense, field_goal), 2393 charted players, matched with the same rules
     the CSV crosswalk uses. That alone got 92%.
  2. `/v1/players?name=` for the leftovers, requiring a full normalized name
     match. Almost all of these are rookies: the directory only contains
     players PFF charted last season, so anyone without snaps is absent from
     it but still has an id.
  3. A surname query requiring surname plus NFL team plus position, for
     nicknames the substring search cannot reach. Riq / Tariq Woolen and Chig
     / Chigoziem Okonkwo are both this case, and both moved teams, which is
     why pass 1's nickname rule missed them too.

One name needed a manual override (Hollywood Brown is PFF's Marquise Brown,
different first initials, so no rule can safely match them) and it lives in
`config/crosswalk_overrides.csv`, which is now a real file. Every D/ST is
unmatched by design: PFF has no team-defense entity in these facets, so they
are counted separately rather than reported as failures.

The file is merged rather than overwritten, because ESPN player ids are global
and both leagues share it. It is in `data/`, which is gitignored, so a fresh
clone rebuilds it with two commands.

**3. Start/sit and player comparison. DONE 2026-09-09.**
`pipeline/usage.py` and `pipeline/startsit.py`, surfaced as
`combine startsit <league> [week]`, `combine compare <league> A B`, and inside
the app's Week mode.

The design decision worth keeping: **the projection and the usage are never
blended.** ESPN's weekly projection is the only weekly number in the system, so
it sets the direction. PFF's usage says whether that number rests on a role the
player actually has, which is what a projection cannot tell you. When they
agree the call is CLEAR; when they disagree that is the finding and it prints
as COIN FLIP, rather than being averaged into a single confident-looking number
that means nothing. Same rule as opinion and news on the draft side.

`usage.py` renders a position-appropriate role line: routes, route rate,
targets a game, YPRR and aDOT for pass catchers; touches, routes, yards after
contact and breakaway rate for backs; dropbacks, YPA, big-time-throw and
turnover-worthy rates for quarterbacks; snaps, tackles, sacks and pressures for
IDP, which RCL needs for half its lineup.

**Opportunities only compare inside a position family.** A tight end's 5.6
targets a game minus a back's 15.9 touches is not a number, and printing it
produced exactly the kind of confident nonsense the VORP fix was about. Across
families the tool says so and falls back to the projection alone.

**A ruled-out starter is worth zero, not his projection.** ESPN keeps
projecting players it has already marked OUT, and an 11-point ghost beats every
healthy bench player, which suppressed the one swap most worth being told
about. `lineup.effective()` is where that lives.

Two constants are picked rather than derived, and are the first things to
recalibrate once there are a few weeks of projected-against-actual: a 3-point
gap counts as CLEAR, a 1-point gap as the floor worth mentioning at all.

Output is deliberately small. Only slots with a real question print, so an
already-correct lineup produces one line saying so. A report you have to scan
is one you stop reading by week 3.

Still owed here: opponent defense strength. The schedule gives us who, not how
hard. `defense/summary` aggregated by team, or the `versus` table inside
coverage_matchup, is where that comes from.

**4. Persist to SQLite. DONE for the training path 2026-09-09.**
`pipeline/history.py`, `combine train build|status|baseline`.

The original tables are still unused and are still built around a canonical
`player_id` ('josh-allen-qb-buf') that nothing produces, and around stat lines
rather than points. The training path needs the opposite, so three new tables
sit beside them rather than retrofitting: `espn_player_week`,
`pff_player_week` and `prediction`. Only raw pulls are stored; features are
computed on read, because the feature set changes on every modelling pass and
stored features would need invalidating each time.

**The find that made the model plan viable:** ESPN serves past seasons, and a
past week carries `projected_points` AND `points` for every rostered player,
already scored under each league's own rules. One row is the label and the
benchmark together. 2025 gave 7752 player-weeks across both leagues, 459
players, 4536 of them started. So "does our model beat ESPN" is a measurable
question on William's exact scoring rather than an argument, and `scoring.py`
is not on the critical path for it after all.

Only rostered players are pulled. Someone had to decide whether to start those
people; nobody was deciding about the free agent pool, so including it would
train on a population the model is never asked about.

The build is resumable, since it is about a hundred requests and some are slow.
Every step skips what is already stored. Resolving 2025's rosters also grew the
id crosswalk from 559 to 796, because rosters churn between seasons.

**5. The residual model. NEXT, and gated.**
`pipeline/training.py` builds the frame, `pipeline/evaluate.py` holds the bar.
No model exists yet, deliberately: the metric was written first, because a
model built first and measured afterwards gets graded against whichever metric
flatters it.

**Target is the residual, `actual - espn_proj`, not the raw score.** ESPN's
projection already absorbs what we cannot see: Vegas lines, beat reporting,
depth chart churn. Trying to out-project it from a few thousand rows loses.
Predicting where it is *wrong* is a much smaller problem, and a model that
learns nothing predicts a zero residual and lands exactly on ESPN, which is the
right failure mode.

**The leakage rule lives in one function.** Every feature for week W comes from
weeks strictly before W, and `training._prior` is the only thing that slices
weeks. A frame that quietly includes W scores brilliantly and is worthless on
Sunday, so `tests/test_training.py` checks it directly.

**The bar, measured on 2025:**

```
ESPN projection        MAE  5.67  RMSE 7.25  pairs 62.5%  close 55.1%
own last 3 weeks       MAE  6.50  RMSE 8.58  pairs 58.4%  close 52.3%
ESPN + recent bias     MAE  6.35  RMSE 8.28  pairs 59.8%  close 52.4%
```

Two metrics because they answer different questions. MAE is how close a number
is. Pairwise decision accuracy is how often the player you were told to prefer
outscored the other, inside one league, week and position family, and that is
the actual job. The CLOSE column is the one that matters: pairs within 3
projected points, where the decision is real. Everyone gets Chase over a backup
right, so overall pairwise accuracy mostly measures how many easy pairs are in
the sample.

**55.1% on close calls is the number to keep in mind.** ESPN is barely better
than a coin flip exactly where the decisions are hard, which is both the
opportunity and the warning. There is room. But anything claiming 70% is
leaking, and the honest ceiling here is a few points, not a transformation.

By family, ESPN is worst at QB (MAE 6.69, close 52.1%) and best at IDP
(MAE 5.07), which is where to look first.

Both no-model baselines lose to ESPN, which is the sanity check that the
harness works.

Still to do: train per-position models on weeks 1-13, validate on 14-18, and
ship only if both metrics beat ESPN out of sample. Then wire it in ARGUE mode
(William's call, 2026-09-09): ESPN's order stands, the model only adds a flag
where it disagrees and why. It graduates to overriding the order once the
`prediction` table says it has earned it.

**6. Remote access.** Tunnel route, WAF rule pinned to Anthropic's egress range
`160.79.104.0/21`, connector registered with a static bearer header. The server
enforces its own token independently of Cloudflare. Do not put a Cloudflare
Access policy on the hostname, it bounces Anthropic with a login redirect and
fails with a useless error.

**7. Yahoo, when approved.** Key and secret into `.env`, run
`scripts/yahoo_login.py`, write the adapter from probe output. Redirect URI must
be `https://localhost:8000`.

**8. Rescore properly.** Write `scoring.py`, apply each league's stat-id scoring
to PFF's raw stat lines, compare against PFF's own `fantasyPoints`. Match means
the shortcut was safe; mismatch means someone has a scoring bug.

**9. Live draft reader.** For next August. ESPN's league API does not expose an
in-progress draft; picks ride a comet channel at `fantasydraft.espn.com`. See
the draft-day notes in project memory.

**10. Discord bot.** Unchanged from the brief.

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
