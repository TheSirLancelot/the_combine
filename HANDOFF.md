# Start here (new chat)

Read this, then `BUILD_GUIDE.md` for detail. `README.md` is user-facing usage.
`fantasy-copilot-brief.md` is the original architecture and is partly outdated,
the build guide says where.

## What this is

A read-only fantasy football co-owner for three leagues. It advises, William
makes every roster move by hand. **Never build, suggest or configure write or
transaction tools against ESPN or Yahoo.** That is the core rule.

Leagues, by slug, which every tool takes:

| slug | platform | format |
|------|----------|--------|
| `rcl` | ESPN | IDP keeper, 0.5 PPR, no K/DST, draft slot 1 |
| `dmwd` | ESPN | full PPR redraft, K + D/ST, draft slot 9 |
| `work` | Yahoo | blocked on API approval, shows SKIP in doctor |

## Where things are

```
src/combine/          config, db (unused), format, cli, server (7 MCP tools)
  platforms/espn.py   real, written from probe output
  platforms/yahoo.py  ping only, blocked
  pipeline/           board, vorp, crosswalk, draftplan, needs, providers/
app.py                Streamlit UI, the primary interface
data/                 gitignored: pff/ exports, opinion/, news/, combine.db
scripts/              probes and the ESPN cookie refresh
```

Run it: `uv run combine doctor --live`, `uv run combine try board dmwd 20`,
`uv run streamlit run app.py`.

## State as of 2026-09-09

Drafts are done. The draft path (board, VORP, tiers, ADP, timing, targets) is
finished and now dormant until next August. **The season is the work now.**

The weekly lineup view is built on branch `season/weekly-lineup`:
`combine week <league> [week]`, and a Week/Draft mode switch in the Streamlit
app. It reads box scores, splits starters from bench, flags problem starters,
and points out bench players outprojecting a starter whose slot they can fill.
That last part runs on ESPN's weekly projection alone and is not yet the
start/sit call.

The PFF API client is built too (`pipeline/providers/pff_api.py`), and the
crosswalk resolves ESPN ids onto PFF ids: `combine pffids <league>`, stored in
`data/crosswalk_pff_ids.csv`, 100% on both leagues and 559 players.

Start/sit and comparison are built on top: `combine startsit <league> [week]`
and `combine compare <league> A B`, plus the same calls inside the app's Week
mode. The projection and the usage are deliberately never blended, and
opportunities only compare inside a position family. See the build guide.

Next up: opponent defense strength (the schedule says who, not how hard), then
persisting actuals to SQLite from week 1 onward, which is the only route to
weighting the blend by measured accuracy rather than by opinion.

## The PFF API, correctly

The brief assumed it was a projections source. It is not. 70 endpoints of
grades and charted stats. No projections, no rankings, no ADP anywhere.
Projections and ADP still come from hand-exported CSVs in `data/pff/`.

Key is in `.env` as `PFF_API_KEY=ak_...`, bearer auth, base `https://api.pff.com`.
Verified working, tier `pro`.

- `/v1/facet/<area>/<report>?league=nfl&season=2025` returns every player, no id
  needed. Receiving gave 792 rows with player_id, name, team, position, grades,
  EPA, routes, route_rate, slot_rate, aDOT, contested catch, drops. Build here.
- `/v1/player/<area>/<report>` requires an explicit `player_id`.
- `/v1/players?name=` is a SUBSTRING name lookup and yields PFF's `player_id`.
- `defense/coverage_matchup` is shaped differently: three lists, and `versus`
  is the receiver-against-defender table. Use `facet_groups()`, not `facet()`.
- Responses run to 4MB and 22s, so the client caches to `data/pff_api/`.

Opponent does not come from the box score. ESPN sends opponent pro-team id 0
there, which espn-api renders as the string `"None"`. The real schedule is the
season-level `proTeamSchedules_wl` view: one request, all 32 teams, every week,
with home/away and kickoff time. `EspnClient.pro_schedule(week)` wraps it, and
bye is now derived from a team having no game rather than from ESPN's flag.
Defensive matchup strength is still owed and should come from PFF's team
defense facets.

PFF's 2026 data is preseason only until games are played, and nothing in a row
says so. `/v1/leagues` does: `default_week` below 1 means preseason. The client
uses that (`season_state().stats_season`) and falls back to last season, which
is why week 1 start/sit leans on 2025 grades as a prior. It reports which
season it used rather than deciding quietly.

**The crosswalk is done.** `combine pffids <league>` resolves ESPN player ids
onto PFF ids and stores them in `data/crosswalk_pff_ids.csv`, merged across
leagues because ESPN ids are global. 100% on both, 559 players. Every D/ST is
unmatched by design, since PFF has no team-defense entity. The projection CSVs
are still matched by name, which is a separate join and unchanged.

## Hard-won gotchas

Write code against probe output, never against documentation. Every adapter
here was built by dumping real responses first, and every time we guessed we
were wrong.

ESPN cookies die mid-season and fail as a 401 or a silently empty league.
`python scripts/refresh_espn_cookies.py` makes recovery a 30 second paste.

ESPN's API does not expose an in-progress draft. Only relevant next August.

VORP, not raw points, orders the board. Comparing ADP (a draft-order number)
against a points rank made every value negative. Replacement level comes from
each league's own slot counts times team count.

The crosswalk refuses to guess. Ambiguous name matches are reported, never
matched. Unmatched players are often genuinely absent from PFF rather than a
matching failure, and that absence is itself a signal.

Opinion (`data/opinion/`) and news (`data/news/`) sit beside the numbers and
never enter the blend. Opinion has no scale or scoring format.

Git in this repo strands `.git/*.lock` files when a session resets. If commits
fail with "another git process seems to be running", delete them.

## Working with William

Experienced engineer, only software person on his team. Skip the hand-holding.

Casual and concise. No hyphens breaking up sentences, minimal bold, go easy on
bullets. He will ask for more if a first pass is too short.

When debugging, follow the causal chain from the root cause forward, one
variable at a time. Do not branch into downstream symptoms before the actual
cause is confirmed.

Secrets live in `.env` only, never in code, chat or committed files.

Keep tool outputs lean. Small decision-ready answers, not raw dumps. That is
both a context-cost rule and a design principle baked into the system.
