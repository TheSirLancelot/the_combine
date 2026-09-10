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

**We did not write `scoring.py` until the Yahoo league forced it.** Both ESPN and PFF hand back projections
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

**5. The residual model. BUILT AND REJECTED 2026-09-09.** It does not ship.
The code stays (`pipeline/model.py`, `combine train model`) because the harness
is reusable and because the next person to have this idea should find the
result rather than repeat the work.

The setup was right. Target is the residual, `actual - espn_proj`, so a model
that learns nothing predicts zero and lands exactly on ESPN. Ridge per position
family, weeks 1-13 to train, lambda chosen on an inner split of weeks 11-13,
scored on weeks 14-18 which were never touched. It declines to predict for
players with under two weeks of history, since a median-imputed feature vector
is a guess dressed as a number.

**Holdout, 2025:**

```
      family  n_train  n_hold  lambda  espn_mae  model_mae  espn_close  model_close
         idp      661     330     300     4.975      4.810       0.566        0.584
pass-catcher    1370     670     300     5.751      5.573       0.566        0.558
          qb     388     200     100     6.931      7.875       0.515        0.529
          rb     850     435      30     6.937      7.098       0.548        0.558
```

Two families improve MAE, two get worse. Bootstrapping over players (not rows,
because one player's weeks are correlated) puts every one of those deltas
inside or barely outside a 95% interval containing zero. Lambda pinning to the
top of the grid for the two biggest families is the same story from another
angle: the fit wants to shrink almost everything to zero, because there is
almost nothing to learn.

**The finding that settles it is the flip test**, which is in `evaluate.py` and
is the metric this project should have reached for first. Global MAE and
pairwise accuracy both average over the pairs where the model AGREES with ESPN,
and agreement is most of them, so a useless model can look level. The flip test
looks only at close calls where the model disagrees and puts the lower-projected
player ahead, which is the entire feature William asked for:

```
  idp            espn 55.7%   flips 1101 (16.7%)   flip accuracy 46.6% +-1.5
  pass-catcher   espn 56.2%   flips 2675 (24.5%)   flip accuracy 48.6% +-1.0
  qb             espn 51.5%   flips  303 (25.9%)   flip accuracy 52.5% +-2.9
  rb             espn 54.8%   flips  990 (31.2%)   flip accuracy 44.0% +-1.6
```

When it overrules ESPN it is right 47.5% of the time overall, worse than a coin
flip and far worse than ESPN's own 55.6% on the same pairs. It disagrees on
roughly a quarter of close calls. Shipping that in ARGUE mode would mean
flagging 1 in 4 decisions with advice that is wrong more often than right.

Where the pass-catcher MAE gain comes from is worth understanding: the model
shrinks projections toward the mean, which improves the average error and
destroys the ordering, and ordering is the whole job. Better number, worse
decisions. That is why MAE alone was never going to be the gate.

**What would change the answer.** More seasons: one season of two leagues is
~3300 usable rows across four families, and the effect being chased is a couple
of points inside a distribution whose weekly noise is 7 points RMSE. The
2026 `prediction` table also has to accumulate before any of this is worth
retrying on live data. And the features are all volume and efficiency; nothing
here knows about snap-count news, injury designations on teammates, or Vegas
totals, which is plausibly where the actual edge is and is not in PFF's data.

**What stays useful.** The measurement harness, the leakage-safe frame, and the
flip test itself, which is now the standard any future model gets held to.

**6. Distribution, posture and the optimizer. SPLIT RESULT 2026-09-09.**
One of the three ships.

**The optimizer ships.** `pipeline/optimize.py`, wired into `combine startsit`
and the app's Week mode. Exact maximum-weight assignment of players to slots by
DP over slot subsets, not the greedy one-for-one check in `lineup.py`, so it
sees moves like "put the receiver in the flex so the second back takes the RB
slot and the tight end comes off the bench". Backtested on 2025 against lineups
as actually fielded:

```
as actually set             406 team-weeks   50.0% win rate   129.3 pts/wk
optimizer on projections    406 team-weeks   53.7% win rate   132.0 pts/wk
```

+3.5pp of win rate and +2.5 points a week, moving someone in 57% of weeks and
worth +4.5 points on those. On William's own 2025 teams it was +52 points in
RCL and +22 in DMWD, one extra win in each. No forecasting is involved: the
same players are valued the same way, they are just assigned better. Locked
players are pinned, since a suggestion you cannot act on is noise, and a
ruled-out starter is worth zero via `effective()`.

**Posture does not ship.** `pipeline/posture.py` and the sweep in
`backtest.py` are kept because the result is worth not rediscovering. The idea
was to rank by ceiling when projected to lose and by floor when projected to
win. It loses at every threshold, and loses monotonically more the more often
it fires:

```
threshold  fired   vs optimizer
      +-4  74.4%      -1.7pp
      +-8  55.7%      -1.0pp
     +-16  27.6%      -0.5pp
     +-25   9.9%      -0.2pp
```

Applying each ranking unconditionally isolates why. Ranking by median is
identical to ranking by projection (+0.0pp), because within a position band the
distribution shift is nearly constant, so it is a monotone transform and cannot
reorder anything. Ranking by ceiling is -1.2pp and by floor -4.7pp. The only
reordering posture can produce is ACROSS position families, and cross-family
calibration was separately measured at about 0.4 points with a standard error
nearly as large. So posture trades away certain expected points for a tail
difference too small to pay for them.

**The distribution itself is kept and displayed**, just never used to rank.
`pipeline/distribution.py` gives empirical floor, median, ceiling, boom and bust
rates by position family and projection band, and start/sit prints them beside
the projection. They are real and informative: at 8 to 16 projected points a
back booms (20+) 15.4% of the time against a receiver's 11.8% and a defender's
9.8%, while the defender busts least. Worth knowing, not worth sorting by.

Also worth carrying forward: a projection is a MEAN, and the distribution is
right skewed. The median 2025 outcome was 1.4 points BELOW projection while the
mean was 0.4 below. "Projected 12" does not mean "expect 12".

**7. The attention threshold. DONE 2026-09-09.** What counts as a gap worth
showing is now measured rather than picked.

Over 174,384 comparable pairs in 2025, the chance the higher projection
actually outscored the other player:

```
gap 0.0-0.5  51.0%     gap 2-3   58.4%
gap 0.5-1.0  53.0%     gap 3-4   61.5%
gap 1.0-1.5  54.5%     gap 4-6   66.5%
gap 1.5-2.0  56.4%     gap 6-8   71.7%
```

The old flat `MIN_EDGE = 1.0` was therefore surfacing 54% calls, which is close
enough to a coin flip to train you to ignore the flags.

Raw points is the wrong unit, which is what William suspected when he asked
whether floor and ceiling had something to do with it. They do. The same two
points means more between two defenders (outcome sd about 6) than between two
backs projected 20+ (10.6). Dividing the gap by the pooled spread of the two
players' outcome distributions lines the curve up across positions, where the
raw gap does not:

```
normalized gap   idp    pass-catcher   qb     rb
0.10-0.15       51.8%      53.0%      49.8%  54.9%
0.15-0.20       54.2%      55.5%      54.4%  55.4%
0.25-0.30       57.9%      56.5%      57.9%  58.7%
0.50-0.75       61.7%      65.3%      62.6%  68.2%
```

`MIN_Z = 0.25` is the knee: every position clears 57% there and the curve
flattens above it. In points that is roughly 1.2 for a low-projected defender
up to 2.6 for a back projected 20+. `MIN_EDGE = 1.0` survives as a floor, since
a sub-point gap is inside the rounding of the projections, and as the fallback
when there is no outcome history to measure spread from.

This is the third use of the same distribution data, and the only one that
worked by changing what gets shown rather than what gets ranked. Worth noting
as a pattern: the outcome spread is useful for deciding whether a difference is
real, and not for deciding which side of it to take.

**Should the threshold be per player? Tested 2026-09-09, no.** Recorded so it
does not get re-tested.

The bar already moves with the two players being compared, through their
projection level rather than their identity:

```
rb   0-6    spread  4.8  edge 1.2      pass-catcher 0-6    spread 5.6  edge 1.4
rb   6-10   spread  6.8  edge 1.7      pass-catcher 6-10   spread 6.2  edge 1.6
rb   10-14  spread  7.8  edge 2.0      pass-catcher 10-14  spread 7.3  edge 1.8
rb   14-20  spread  9.1  edge 2.3      pass-catcher 14-20  spread 8.6  edge 2.2
rb   20+    spread 10.6  edge 2.6      idp   10-14         spread 6.3  edge 1.6
```

Two questions were asked of the 2025 data about going further.

*Does a player's own past volatility predict his future volatility?* Splitting
each player into the calmer or wilder half of his position and band by prior
residual spread, then measuring what he did next: 7.80 against 7.26. Real but
small, and inconsistent in sign. Backs show +1.59, quarterbacks REVERSE at
-0.94. It also needs five or more prior weeks, so it does nothing in September,
which is when the tool is least sure of itself anyway.

*Do structural traits predict spread within a projection band?* Thirteen tests
across the four families. One clears two standard errors convincingly, RB grade
at +0.99 +-0.30, which is roughly what thirteen tests produce by chance. The
notable null is aDOT for pass catchers at +0.23 +-0.21: the textbook boom-bust
marker does nothing here.

That null explains the whole result. Conditioning on the projection has already
absorbed the trait, because ESPN projects the deep threat differently from the
possession receiver in the first place. It is the same reason the residual model
failed, arriving from the variance side instead of the mean side.

Even taking the one real effect at face value, RB grade would move the bar from
1.89 to 2.14. A quarter of a point, against a band effect already spanning 1.2
to 2.6, in exchange for a per-player dependency that needs half a season to
warm up. Not worth it.

**8. scoring.py and the hand-entered Yahoo league. DONE 2026-09-09.**
The Yahoo API is still in review, so the league was entered by hand:
`config/work_league.toml` for settings and scoring, `config/work_roster.csv` for
the roster, and `platforms/manual.py` to make it look enough like a league that
the week view, the optimizer and the comparison threshold all work unchanged.

Projections are built rather than fetched. ESPN publishes a raw projected stat
line per player per week that is league independent, so we score that line under
Yahoo's rules. The numbers will not match Yahoo's display, which is expected:
this is ESPN's view of the player priced by Yahoo's rules, and the upside is
that all three leagues use one methodology.

**The vocabulary is ESPN stat IDs, not names, and this is the whole lesson.** A
name-keyed table looks completely reasonable and validated at 92% on DMWD and 0%
on RCL. Three separate causes, each found by validating rather than by reading:

  * espn-api's id-to-name map does not cover every stat a league can score. RCL
    pays a point per 25 passing yards (id 8) and per 10 rushing yards (id 28),
    DMWD pays 5 for a 50+ field goal (id 198), and none of those have names. The
    stat lines carry them as bare numeric keys for exactly that reason, so a
    name-keyed table dropped them and every RCL quarterback came out 11 light.
  * Six names map to TWO ids each: passingYards is 3 and 22,
    receivingReceptions is 41 and 53. Which one a league scores is a property of
    the league, so a single reverse map cannot be right for everyone. Collapsing
    them cost every DMWD quarterback 15 points.
  * ESPN projects defensive stats for two-way players, and pays them only where
    the league has IDP slots. Travis Hunter is the worked example. Gating on
    `idp` fixed it, and then over-gating made every kick returner project low,
    because return touchdowns (ids 101, 102, 93) sit in the same id band as
    defensive stats but a receiver earns them.

`combine scoring` is the standing proof: score ESPN's stat lines under each ESPN
league's own rules and compare against the points ESPN published. Currently
209/209 exact on RCL and 184/185 on DMWD, the exception being Travis Hunter at
0.054. A hand-entered table cannot be validated against its platform, but the
engine under it can be, which is the strongest guarantee available here.

**What the Yahoo table cannot price**, listed in the file itself so the omission
is visible: 40+ yard non-TD completions, runs and receptions (ESPN projects only
the 40+ yard TD versions), pick sixes thrown, return TDs by offensive players,
and D/ST fourth-down stops and three-and-outs. The quarterback gap is the
largest at roughly 1-2 points a game; the D/ST gaps under-count every defense by
a similar amount. Near constant within a position, so they move totals more than
ordering. One approximation: Yahoo's points-allowed tiers break at 20/21 and
ESPN projects an 18-21 bucket, so it is split three quarters into the 1-point
tier.

**What this league's scoring does to it.** Completions at a full point each,
confirmed with the GM, makes quarterbacks enormous: Kyler Murray projects 39.4
where ESPN's own scoring would say about 19. It also means a backup quarterback
is worth more on the bench than a skill player, which is a roster-construction
fact rather than a bug. Sacks were confirmed as changing to -1, and the table
reflects that.

**Two bugs the hand-entered league exposed in shared code.** `BENCH_SLOTS` knew
ESPN's "BE" but not Yahoo's "BN", so every bench player counted as a starter.
And the week view printed a matchup line and a projected margin against an
opponent that does not exist; both are now suppressed when there is no opponent
lineup. Entering an opponent's whole roster every week is more upkeep than the
matchup line is worth, so that stays empty until the API lands.

**9. Remote access.** Tunnel route, WAF rule pinned to Anthropic's egress range
`160.79.104.0/21`, connector registered with a static bearer header. The server
enforces its own token independently of Cloudflare. Do not put a Cloudflare
Access policy on the hostname, it bounces Anthropic with a login redirect and
fails with a useless error.

**10. Yahoo, when approved.** Key and secret into `.env`, run
`scripts/yahoo_login.py`, write the adapter from probe output. Redirect URI must
be `https://localhost:8000`.

**11. Rescore properly.** Write `scoring.py`, apply each league's stat-id scoring
to PFF's raw stat lines, compare against PFF's own `fantasyPoints`. Match means
the shortcut was safe; mismatch means someone has a scoring bug.

**12. Live draft reader.** For next August. ESPN's league API does not expose an
in-progress draft; picks ride a comet channel at `fantasydraft.espn.com`. See
the draft-day notes in project memory.

**13. Discord bot. DONE 2026-09-10.** `src/combine/bot.py` plus
`src/combine/discord_out.py`, run with `combine bot` or the launchd agent in
`scripts/com.thecombine.bot.plist`.

Chosen over exposing the Streamlit app through the Cloudflare tunnel, and it
replaced that plan rather than adding to it. The reason is the direction of the
connection: the bot dials OUT and holds a websocket, so there is no public
hostname, no ingress rule, no Access policy whose correctness matters and no
inbound surface. It also collapses both wanted behaviours into one process,
slash commands for asking and a scheduled check for being told.

Commands are `/week`, `/startsit`, `/waivers`, `/scoreboard`, `/compare`,
`/glossary`, `/health`, `/clear`, locked to
`DISCORD_OWNER_ID`. Read-only, and more emphatically than anywhere else in the
repo, because this is the one component that takes instructions from a chat box.

**Embeds and buttons, 2026-09-10.** Every command returns
`list[discord.Embed]` instead of `list[str]`, built in `discord_out.py`.

Three decisions worth keeping.

Colour is severity, not palette: GOOD, INFO, WARN, BAD, DEAD, picked so the bar
answers "do I need to act" before the message is opened. DEAD is grey and is used
for a league that cannot answer, like Yahoo having no free agent pool. Using red
there would have trained him to ignore red, which is the only thing the colour is
for.

The week and scoreboard views are several stacked embeds rather than one, and the
reason is Discord's render order: within an embed the description always comes
before the fields, so one embed puts the lineup table above the score. The score
is what the message is opened for. Separate embeds stack in the order given.

`field()` enforces three caps and returns whether the field went in. Twenty-five
fields, 1024 characters in a value, and 6000 characters across the whole embed
added together. The last one is the dangerous one: Discord rejects an oversized
embed outright, so the message never arrives, and the symptom is silence, which
is exactly what a normal quiet week looks like. A test found it, not production.
The budget is 5500 rather than 5900 because footers are set after the fields are
added, so `len(embed)` at measuring time does not include one yet. Where notes do
not all fit, the footer says "notes shown for 3 of 8" rather than dropping them
silently.

Buttons are a `discord.ui.DynamicItem`, not a plain `View`. A normal view lives in
the process that sent it, so every button in the channel goes dead on restart and
a button that silently does nothing is worse than no button. The whole state --
command, league, target week -- is encoded in the custom_id, so the handler is
rebuilt from the click. `add_dynamic_items(Nav)` in `setup_hook` is what registers
it; without that line the buttons are decoration. The owner check is repeated in
the button callback, because anyone who can see the message can click it and the
slash command's check does not carry over to a component interaction.

`embed_text()` flattens an embed back to text. Nothing in Discord needs it; it
exists so `combine notify --dry-run` can still show the wording of a message that
fires once a week, and so the tests can assert on content.

`/clear`, added 2026-09-10, is the single exception and it is worth being precise
about why it is not a violation. The read-only rule exists because a write
against ESPN or Yahoo is a roster move with real consequences that only William
should make. `/clear` writes to DISCORD, deleting the bot's own status posts out
of its own channel. Different blast radius entirely. It is still irreversible, so
it is built with the guards that implies: a dry run by default, `confirm: True`
required to delete, ephemeral replies so the progress message cannot be caught in
its own purge, and an explicit `discord.Forbidden` branch that names the two
permissions and points at server settings rather than the developer portal, which
is the mistake this cost an exchange to sort out.

It is also the one command that does not route through `respond()`. That helper
exists to keep blocking ESPN calls off the event loop; `purge` is async I/O
against Discord and belongs on the loop as it is. The type guard checks for
`TextChannel | Thread` rather than `Messageable`, because a DM can be read but
not purged.

**Getting it running under launchd cost two rounds, both self-inflicted.** First,
`discord.py` went into `pyproject.toml` and never into `uv.lock`, so `uv run`
had nothing to install; testing had been done by importing the module in a venv
where it was hand-installed, which bypassed the lockfile entirely and made a
broken path look fine. Second, the command was wrapped in `/usr/bin/taskpolicy
-b` for background QoS, which `ProcessType: Background` already provides, so it
was a redundant binary in the exec path. When an exec fails, launchd writes to
the system log and NOTHING to the job's own log files, so the symptom was two
empty log files and no explanation. Empty logs plus a job that is not listed now
means exactly that, and `install_agents.sh` says so and prints the system-log
query.

Three things learned by connecting rather than by reading:

  * **`Intents.none()` is wrong.** `guilds` is not a privileged intent and is
    required for the channel cache; without it `get_channel` returns None and
    the scheduled check posts nowhere while logging a warning nobody reads.
    Caught by a connection test that printed what the client could actually see.
    `weekly_check` also falls back to `fetch_channel`, which goes over REST and
    does not depend on the cache at all.
  * **Send Messages is per channel.** The bot had the server permission and a
    channel override denied it. Slash commands still worked, because interaction
    replies use a webhook token and bypass channel send permissions, so the
    failure mode is a bot that answers every command and never posts on
    schedule.
  * **Width, not length, is the constraint.** Measured first: the week view is
    1353 characters over 27 lines, comfortably inside Discord's 2000 cap, but
    its widest line is 74 and start/sit reaches 130. Discord wraps code blocks
    rather than scrolling them, so tables are re-rendered at about 48 characters
    and prose is left as markdown for Discord to wrap.

Two mechanics that are easy to get wrong and are commented in the module: every
command defers before working, because Discord wants a response inside three
seconds and this pipeline takes several; and the work runs in a thread, because
discord.py has one event loop and blocking it stops the heartbeat and drops the
gateway connection.

`combine notify` runs the check on demand: `--dry-run` prints without sending,
`--force` posts even when nothing is wrong. That last one is not a convenience.
The check's success condition is silence, so a quiet week and a broken bot look
identical, and forcing a labelled test post is the only way to prove delivery
works. It sends over REST via `login` and `fetch_channel` without joining the
gateway, because a second gateway session while the agent is running would be a
second copy of the bot answering every command twice.

Silence is the feature in the scheduled check. A correct lineup produces no
message, because a bot that says "nothing to report" every week gets muted and
then the one week it matters is missed.

## Scoreboard

`pipeline/scoreboard.py`, added 2026-09-10. Every league's matchups in one place,
as `combine scoreboard`, `/scoreboard` in Discord and a Scores mode in the app.
`EspnClient.all_matchups` was added alongside it, since everything before this
only ever needed my own box score.

Two derived numbers earn their place next to the score. Starters whose game has
not ended, because a 20 point lead with nine players left is not a lead. And
ESPN's projected final, which moves during games as their model updates, so it is
labelled as ESPN's rather than presented as ours.

Three pieces of derived state that are easy to get wrong on a Sunday morning when
every score is legitimately zero, and are therefore tested directly: started
comes from points scored OR being final, final comes from players remaining
rather than from a non-zero score (a defense can score nothing all day), and
`margin` reads from my side when I am in the game and falls back to home minus
away when I am not.

The hand-entered Yahoo league cannot appear, because a scoreboard needs the
opponent's lineup and entering one weekly by hand is more upkeep than a score
line is worth. It renders as unavailable WITH the reason rather than being
filtered out, so its absence never reads as a bug.

## The waiver wire

`pipeline/waivers.py` for the live question, `pipeline/wire.py` for what it was
worth, added 2026-09-10. `combine waivers` and `combine train wire --all-teams`.

**What the backtest can and cannot establish.** ESPN does not retain historical
weekly PROJECTIONS: for any player in any past week `projected_points` comes back
None, verified across a batch of 60. Projections survive only for players someone
rostered, because we stored them week by week as the season ran. So the method's
ranking cannot be replayed against the past and this is NOT the flip test the
residual model got.

What can be measured exactly is availability, since every roster was stored for
every week, and actuals, since ESPN answers `player_info` with a list of ids and
returns every week at once already scored under that league's own rules.

So the substitute is an ex-ante rule that needs no projections: each week take the
available player with the best trailing form and see what he actually did.
Trailing form is a weaker signal than a projection, so this is a FLOOR on what
the live version can manage, not an estimate of it.

**Result, 432 team-weeks across both leagues:**

```
RCL    +2.50 points a week, se 0.28  (8.8 sigma)   helped 38% of weeks, changed 5 of 216 results
DMWD   +0.79 points a week, se 0.22  (3.7 sigma)   helped 10% of weeks, changed 2 of 216 results
```

Both real. The asymmetry matches the live tool exactly: RCL's deep IDP pool and
weak DP slot produce three candidates in week 1 while DMWD produces none.

Two things that number does not include. A bad add never costs points in the
week, because you simply do not start him, so the downside here is structurally
invisible; what it really costs is the dropped player's future, which this does
not measure. And the hindsight ceiling, 21 points a week in RCL, is not an
opportunity figure at all: the best of 300 players is high by arithmetic, and it
mostly measures pool size.

**A profiling lesson worth keeping.** The sweep would not finish, and the cause
was a `best_lineup` call sitting inside a list comprehension's CONDITION, so it
re-ran once per roster player: 21 solves a week instead of 1. Reading the code
twice did not find it; `cProfile` found it in a minute. The same solve was
already being computed one line above.

**Surfacing it, 2026-09-10.** `/waivers` in Discord (optional league, all three
when omitted), a Waiver wire section at the bottom of the app's Week page, and
folded into the scheduled check.

The notification gate is `worth_telling = any(not c.trades_down for c in found)`.
An add that gains the week but costs season value is real advice and belongs in
`/waivers`; it is not worth interrupting a morning for, because the answer
depends on how the rest of your season looks and only you know that. In RCL week
1 the top candidate trades down and the other two do not, so the check does ping,
and it pings for the two that are unambiguous.

All three surfaces are a table plus numbered notes. The numbers belong in
columns — WEEK and SEASON both signed, since the whole point is that they can
disagree — but the caveats do not fit in a column and truncating one would leave
a confident number with its qualifier cut off. So the caveats go underneath,
keyed by row number, and the Discord table is 38 characters wide to survive phone
wrapping. Names go through `short_name()` for the same reason: a hard truncation
to column width gave "DeForest Buck", which is both ugly and ambiguous.

The app caches waivers at `ttl=300`. The wire does not move minute to minute and
the sweep scores a 350-player pool, so a five minute cache is the difference
between a page that loads and one you wait on.

## Daily instead of Sunday

Changed 2026-09-11. `weekly_check` is now `daily_check` and the `CHECK_DAYS`
gate is gone. Games run Thursday through Monday, so a Sunday-only check missed a
Thursday injury and every waiver window that opened midweek.

The interesting part is what makes daily survivable. The original gate was "post
only when there is news", which was enough at once a week, but news does not stop
being news the next morning: a starter ruled out for the season would have posted
the same card six days running, and a channel that repeats itself gets muted --
at which point the one message that mattered is missed too.

So a builder now returns a `Report(embeds, news, signature)`. The signature is
what the report is ABOUT and deliberately carries no numbers: `hurt:<player_id>`,
`swap:<bench_id>><starter_id>`, `add:<name>><drop>`. `already_said()` digests it
and compares against `data/last_post.json`, keyed by league and week. Digesting
the rendered text instead would have defeated the whole thing, because ESPN
revises projections through the day and every morning would have looked new.

Failures are deliberately one-directional. An unreadable or unwritable state file
returns False and posts, so the worst case of this cache is a duplicate message
rather than a missed one. An empty signature is never suppressed either, since
that means the builder could not say what the report was about.

## Waiver drops you are actually allowed to make

Fixed 2026-09-11, from a real bad recommendation: it told him to drop Rashid
Shaheed on a day Shaheed had already played, which the platform will not allow.

`find()` now excludes any player whose game has kicked off from the drop
candidates, and keeps the one it WOULD have picked so the message can name him
and price the difference. `blocked_cost` is the extra season value surrendered by
being forced onto a legal drop, and `blocked_note()` says both that number and
whether the move still gains season value overall. No threshold on "is the
difference big enough" -- that is his call and the two numbers are what it turns
on. When nothing on the roster can be dropped at all, `drop_locked` says so
rather than the tool recommending the impossible.

Writing the test for that found a second bug in the same function. The cheap
pre-filter compared a candidate against the weakest starter in each slot he was
eligible for, defaulting a slot with no entry to infinity -- so an EMPTY starting
slot rejected everyone, when in fact an empty slot is beaten by anybody at all.
The fix is `setdefault(slot, 0.0)` AFTER the loop over starters, not as the
default inside it: seeding zeros first makes every `min()` zero and turns the
filter off completely, which is a quiet way to go back to returning 94
candidates. Both directions are tested now.

## Known soft spots

**Streamlit caches survive code changes, and that bit us.** Streamlit
re-executes `app.py` when you save but keeps already-imported modules and their
cached return values. Adding a field to the `Band` dataclass therefore put new
code in front of objects built by the old code, and the running app raised
`'Band' object has no attribute 'spread'` for a field that did exist in the
source. Restarting fixed it, which is exactly what makes this kind of bug
expensive: the code was correct and the failure looked like a code error.

Two changes so it cannot recur. `_code_version()` in `app.py` hashes the mtimes
of every module under `src/combine` and is passed to every cached function, so
any edit invalidates every cache. And the outcome history is now cached as a
plain DataFrame with the `Distribution` rebuilt per run, rather than caching the
object itself, because caching data is version-proof in a way that caching
objects is not.

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
