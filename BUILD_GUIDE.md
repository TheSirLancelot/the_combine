# The Combine — build guide

Companion to `fantasy-copilot-brief.md` (the architecture) and `README.md` (how
to use it). This is what got built, where it diverged from the original plan
and why, and what is still owed.

Last updated 2026-09-03, three days before drafts.

---

## Status

**Working today, shell only.** Live ESPN league state for both leagues, PFF
projections and ADP, a merged draft board ranked by value over replacement, a
snake-draft timing tool, a roster-aware needs view, analyst sentiment and
injury news. All of it runs from `uv run combine try ...`.

**Not built yet.** The tunnel, the WAF rule, and the connector registration.
Nothing is reachable from a phone. That was Phase 1 in the original plan and it
is now the largest outstanding piece.

**Blocked.** Yahoo. Their Fantasy Sports API now sits behind a manual review,
applied 2026-09-02, quoted at 1-2 weeks. The `work` league shows as SKIP in
`doctor`, which is expected rather than broken.

---

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

**1. After the draft: roster and matchup.** `get_my_roster` works but has
nothing to show yet. `matchup` raises on purpose, since `box_scores()` 404s in
preseason. Both become writable and testable the moment week 1 has real data.
This is the smallest, highest-value next step.

**2. Remote access.** Tunnel route, WAF rule pinned to Anthropic's egress
range `160.79.104.0/21`, connector registered with a static bearer header. The
server already enforces the token itself, independent of Cloudflare, so a
tunnel misconfiguration is not a breach. Do not put a Cloudflare Access policy
on the hostname; it will bounce Anthropic with a login redirect and fail with a
useless error. Running as a long-lived process also removes the per-command
cold start, which is a second or two of re-fetching league settings on every
CLI invocation.

**3. Persist to SQLite.** Specifically the `actual` table, weekly, starting
week 1. It costs nothing now and it is the only way to ever answer the
weighting question from the brief with data instead of opinion. Everything else
can stay in memory until there is a second consumer.

**4. Yahoo, when approved.** Consumer key and secret into `.env`, run
`scripts/yahoo_login.py`, implement the adapter against probe output the same
way ESPN was done. Redirect URI must be `https://localhost:8000`.

**5. Rescore properly.** Write `scoring.py`, apply each league's stat-id
scoring to PFF's raw stat lines, and compare the result against PFF's own
`fantasyPoints`. If they match, the shortcut was safe and we gain the ability
to add sources that only publish stats. If they do not, we have found a bug in
someone's scoring, which is worth knowing either way.

**6. Weight the blend.** Equal weighting is currently a necessity, not a
choice: two sources and no track record. Once `actual` has a season of data,
weight by measured accuracy per source and per position.

**7. Discord bot.** Unchanged from the brief. Reads the same store, never
touches ESPN or Yahoo directly, push notifications only.

---

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
every defender has no ADP and no VAL. Whether ESPN's free-agent pool correctly
excludes kept players has not been verified.

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
