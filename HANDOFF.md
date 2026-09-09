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

Next up, in order: a PFF API client with the crosswalk extended to PFF player
ids, then start/sit and player comparison, which is what William actually
asked for.

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
- `/v1/players?name=` is name lookup and yields PFF's `player_id`, which is the
  anchor for extending the crosswalk to a third source.
- 2026 shows `default_week: -4` (preseason) until games are played.

Opponent does not come from the box score. ESPN sends opponent pro-team id 0
there, which espn-api renders as the string `"None"`. The real schedule is the
season-level `proTeamSchedules_wl` view: one request, all 32 teams, every week,
with home/away and kickoff time. `EspnClient.pro_schedule(week)` wraps it, and
bye is now derived from a team having no game rather than from ESPN's flag.
Defensive matchup strength is still owed and should come from PFF's team
defense facets.

PFF's 2026 data is preseason only until games are played. `season=2026&week=1`
returns zero rows; `season=2025` is full. Week 1 start/sit leans on 2025 grades
as a prior, and the client needs an explicit season/week policy rather than
defaulting to the current season.

**Do the crosswalk before start/sit.** Right now ESPN and PFF are matched by
name, conservatively, at about 97%. The API gives a stable `player_id`, so
resolve ESPN players to PFF ids once and store the mapping rather than adding a
third name-matched source. Doing this after start/sit is built means unpicking
it later.

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
