# The Combine — step by step build guide

Companion to `fantasy-copilot-brief.md`. The brief is the architecture. This is the order of operations, with the decisions locked in and the actual mechanics of each step.

## Decisions locked

**Auth: static bearer header + Cloudflare WAF IP allowlist.** Claude supports fixed-credential auth for custom connectors via `static_headers` (beta). An org admin (you) enters the credential once when adding the connector and Claude sends it on every request. Standard header names (`authorization`, `x-api-key`) are accepted without review. Anthropic's outbound traffic comes from `160.79.104.0/21`, a fixed published range, so you can pin a WAF rule to it. Note that range is shared across all Anthropic customers, so the IP rule alone is not authentication. You need both. If `static_headers` isn't exposed in your connector UI yet, the fallback is a long random path segment on the endpoint (`/mcp/7f3a9c...`) plus the IP rule, and you rotate the path instead of a token. Never a query-string token.

**Store: SQLite.** One file at `data/combine.db`. WAL mode so the pipeline can write while MCP tools read.

**MCP servers: thin, ours.** Community forks tend to expose broad dump-everything tools, which is the exact token problem the brief is built to avoid.

**Design change I'm proposing.** The brief has stdio servers wrapped in supergateway/mcp-proxy for HTTP. If we're writing the servers ourselves, skip that layer entirely. FastMCP speaks streamable HTTP natively, so a gateway container adds a hop, a failure mode, and nothing else. Same for the ESPN/Yahoo split: one server process, one connector, with a `league` argument on every tool and platform adapters behind a common interface. Three leagues from one connector, and the token-scoping rule (one league per query) becomes a required parameter instead of a convention. If you'd rather keep two connectors for blast-radius reasons, the code below splits cleanly, but I'd start with one.

---

## Phase 0 — repo skeleton

```
the_combine/
  pyproject.toml
  .env                  # gitignored
  .env.example          # committed, keys only
  data/                 # gitignored, holds combine.db
  src/combine/
    __init__.py
    config.py           # env + league registry
    db.py               # sqlite connection, schema, migrations
    platforms/
      __init__.py       # LeagueClient protocol
      espn.py
      yahoo.py
    server.py           # FastMCP app + tools
    pipeline/
      __init__.py
      crosswalk.py
      providers/
        __init__.py     # Provider protocol + normalized shape
        espn_proj.py
        yahoo_proj.py
        own_model.py
      scoring.py
      blend.py
      run.py            # entrypoint the scheduler calls
  scripts/
    refresh_espn_cookies.py
    yahoo_login.py
```

```bash
cd ~/the_combine
git init && printf '.env\ndata/\n__pycache__/\n*.pyc\n.venv/\n' > .gitignore
uv init --package . 2>/dev/null || python3 -m venv .venv
uv add fastmcp espn-api yahoofantasy python-dotenv rapidfuzz
```

`.env.example`:

```
COMBINE_BEARER_TOKEN=
ESPN_S2=
ESPN_SWID=
ESPN_LEAGUE_1_ID=
ESPN_LEAGUE_1_TEAM_ID=
ESPN_LEAGUE_2_ID=
ESPN_LEAGUE_2_TEAM_ID=
YAHOO_CONSUMER_KEY=
YAHOO_CONSUMER_SECRET=
YAHOO_LEAGUE_ID=
YAHOO_TEAM_KEY=
SEASON=2026
```

Generate the bearer token now: `openssl rand -hex 32`.

`config.py` holds a league registry keyed by short slug, because every tool takes that slug and you don't want league IDs in prompts:

```python
LEAGUES = {
    "dynasty":  {"platform": "espn",  "id": env("ESPN_LEAGUE_1_ID"),  "team": env("ESPN_LEAGUE_1_TEAM_ID")},
    "work":     {"platform": "espn",  "id": env("ESPN_LEAGUE_2_ID"),  "team": env("ESPN_LEAGUE_2_TEAM_ID")},
    "college":  {"platform": "yahoo", "id": env("YAHOO_LEAGUE_ID"),   "team": env("YAHOO_TEAM_KEY")},
}
```

Rename those slugs to whatever you actually call the leagues. You'll be typing them into Claude constantly.

---

## Phase 1 — live league access

Goal: ask Claude "who should I start at flex in dynasty" from your phone and get a real answer off live rosters. Ship this before any projection work.

### 1.1 Credentials

**ESPN.** Log into fantasy.espn.com in a browser, DevTools, Application, Cookies, copy `espn_s2` and `SWID` (keep the braces on SWID). Into `.env`. These die mid-season, usually without warning, and the failure looks like a 401 or an empty league rather than a clean error. Phase 1.8 adds a health check so you find out on a Tuesday instead of at 10am Sunday.

**Yahoo.** Register an app at the Yahoo developer portal, Fantasy Sports read scope, redirect URI `https://localhost:8000`. That gives you a consumer key and secret. Then run the one-time browser flow, which writes a token file you gitignore:

```bash
python scripts/yahoo_login.py   # thin wrapper over yahoofantasy's login flow
```

Yahoo's refresh token is long-lived and the library refreshes access tokens on its own, so this is genuinely a once-a-year problem, unlike ESPN.

### 1.2 Platform adapters

Define the interface first, then satisfy it twice. This is the same discipline as the provider model in the brief, applied one layer up.

```python
# platforms/__init__.py
class LeagueClient(Protocol):
    def scoring_rules(self) -> dict: ...
    def roster_slots(self) -> dict: ...
    def my_roster(self) -> list[PlayerState]: ...
    def matchup(self, week: int) -> Matchup: ...
    def free_agents(self, position: str | None, limit: int) -> list[PlayerState]: ...
    def injuries(self) -> list[PlayerState]: ...
```

`PlayerState` is a small dataclass: `player_id, name, team, pos, slot, status, opponent, platform_proj`. Nothing else. Every field you add here gets multiplied by every player in every tool response for the rest of the season.

ESPN implementation wraps `espn_api.football.League(league_id=..., year=..., espn_s2=..., swid=...)`, instantiated per league ID as the brief notes. Yahoo wraps `yahoofantasy`. Cache the `League` object per process with a short TTL, since ESPN's client refetches aggressively.

Do not write anything that posts. No `add_player`, no `set_lineup`, no trade tools. Read-only is the rule and it's easiest to keep by never writing the method in the first place.

### 1.3 Tool surface

Six tools, all league-scoped, all capped. Resist adding a seventh until you've missed it twice.

```python
get_my_roster(league)                      -> starters + bench, one line each
get_matchup(league, week=None)             -> both lineups, projected totals
get_top_free_agents(league, position, limit=10)
get_player(league, name)                   -> one player, full detail
get_league_settings(league)                -> scoring + roster slots, cached
health_check()                             -> per-league auth status
```

Output shape matters more than tool count. Return compact structured text, not nested JSON. A roster should be about 20 short lines, not 8KB of objects:

```
QB  Josh Allen        BUF  vs MIA   proj 21.4  OK
RB  Bijan Robinson    ATL  @ TB     proj 17.2  OK
RB  --empty--
WR  Puka Nacua        LAR  vs SEA   proj 15.8  Q
```

Two rules that do most of the work. Cap every list-returning tool with a `limit` that defaults low and is enforced server-side, and filter server-side before serializing (ESPN's `X-Fantasy-Filter` header lets you ask for a position slice instead of pulling 300 players and slicing in Python).

Docstrings are the prompt. Write them so Claude picks the right tool without trying two first. Say explicitly in `get_top_free_agents` that it returns waiver candidates ranked by projection, and in `get_player` that it's for one named player.

### 1.4 Serve it

```python
# server.py
from fastmcp import FastMCP
mcp = FastMCP("combine")
# ... @mcp.tool definitions ...
if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8787, path="/mcp")
```

Bind to loopback. The tunnel is the only way in.

Auth middleware: reject any request whose `Authorization` header isn't `Bearer {COMBINE_BEARER_TOKEN}`, using `hmac.compare_digest`. Do this in the ASGI app rather than per tool so there's no path around it. Cloudflare will also check, but the server enforcing it independently means a tunnel misconfiguration isn't a breach.

Run it under launchd (not Docker, since it's a single Python process and you want it reading the same `data/combine.db` the pipeline writes). A `KeepAlive` plist in `~/Library/LaunchAgents/` with `RunAtLoad`, stdout and stderr to `logs/`.

### 1.5 Tunnel

Add a route to the existing tunnel config, same pattern as your *arr services:

```yaml
ingress:
  - hostname: combine.yourdomain.com
    service: http://127.0.0.1:8787
```

`cloudflared tunnel route dns <tunnel> combine.yourdomain.com`, restart the tunnel, confirm `curl -H "Authorization: Bearer $TOKEN" https://combine.yourdomain.com/mcp` gets past the proxy.

Critically: do **not** put a Cloudflare Access policy on this hostname. An email-gated Access app will bounce Anthropic's requests with a login redirect and the connector will fail with a generic unreachable error that tells you nothing.

### 1.6 WAF rule

Security, WAF, custom rule on `combine.yourdomain.com`:

```
(http.host eq "combine.yourdomain.com" and not ip.src in {160.79.104.0/21}) -> Block
```

Add your home IP to that set while developing, or you'll lock yourself out of your own curl tests. Take it back out when you're done.

### 1.7 Register the connector

claude.ai, Settings, Connectors, Add custom connector. URL is `https://combine.yourdomain.com/mcp`. Under the header/credential option, `Authorization` = `Bearer <token>`. Connect once on web and it's available on iOS and Android on the same account, no per-device setup.

If the header field isn't there, fall back to the secret path segment described up top and mount the app at `/mcp/<random>` instead.

### 1.8 Verify, then harden

Ask Claude "what's my roster in dynasty" from your phone. Then ask for all three leagues in one message and watch what it does; if it fans out to three calls and the context balloons, tighten the tool docstring to say one league per call.

Then add the ESPN cookie watchdog. A daily launchd job that calls `health_check()` locally and, on failure, does something you'll actually notice. Email via `mail`, a ntfy push, whatever. Pair it with `scripts/refresh_espn_cookies.py` that takes the two cookie values on stdin and rewrites `.env` in place, so the recovery is a 30-second paste rather than a debugging session.

**Phase 1 is a working system.** Draft prep and start/sit off live state, no projections yet. Stop here and use it for a week before starting phase 2.

---

## Phase 2 — crosswalk and normalization

This is the actual engineering. Everything after it is config.

### 2.1 Schema

```sql
CREATE TABLE player (              -- canonical identity
  player_id   TEXT PRIMARY KEY,    -- our ID, e.g. 'josh-allen-qb-buf-1996'
  full_name   TEXT NOT NULL,
  pos         TEXT NOT NULL,
  team        TEXT,
  birthdate   TEXT
);

CREATE TABLE player_alias (        -- every source's ID for that player
  source      TEXT NOT NULL,       -- 'espn' | 'yahoo' | 'own' | 'pff'
  source_id   TEXT NOT NULL,
  player_id   TEXT NOT NULL REFERENCES player(player_id),
  source_name TEXT,
  PRIMARY KEY (source, source_id)
);

CREATE TABLE projection (          -- normalized STAT LINES, never points
  source      TEXT NOT NULL,
  player_id   TEXT NOT NULL,
  season      INTEGER NOT NULL,
  week        INTEGER,             -- NULL = season-long
  stat        TEXT NOT NULL,       -- 'pass_yd','rec','rush_td',...
  value       REAL NOT NULL,
  pulled_at   TEXT NOT NULL,
  PRIMARY KEY (source, player_id, season, week, stat)
);

CREATE TABLE league_scoring (
  league      TEXT NOT NULL,
  stat        TEXT NOT NULL,
  points      REAL NOT NULL,
  PRIMARY KEY (league, stat)
);

CREATE TABLE actual (              -- the accuracy log, populated weekly
  player_id TEXT, season INTEGER, week INTEGER, stat TEXT, value REAL,
  PRIMARY KEY (player_id, season, week, stat)
);
```

`projection` storing stats rather than points is the load-bearing decision. It's what lets one adapter serve all three leagues, and it's why `actual` can be compared to it later for accuracy weighting.

### 2.2 The crosswalk

Automated pass then a manual override file, as the brief specifies. Match key is normalized name plus position plus team, in that priority:

1. Exact match on normalized name (lowercase, strip punctuation and suffixes like Jr/III, collapse whitespace) plus position.
2. Same, ignoring team, if the name+pos pair is unique on both sides. Handles in-season team changes.
3. Fuzzy match with `rapidfuzz` at a high threshold (start at 92), only within the same position, and only when the best match beats the runner-up by a clear margin. Otherwise it's not a match, it's a guess.
4. Everything left over goes into `crosswalk_unmatched.csv` for you.

The override file is committed and hand-edited:

```csv
source,source_id,player_id,note
espn,4429795,marvin-harrison-wr-ari-2002,rookie name collision with HOF dad
yahoo,40021,,IGNORE  # yahoo DST duplicate
```

Overrides apply after automated matching and win unconditionally. Run the crosswalk as its own command so you can iterate on it without running the whole pipeline, and have it print a one-line summary: matched, overridden, unmatched. When unmatched is under about five, you're done for the week.

Defense and kickers will fight you. Simplest answer is to canonicalize team defenses as `def-<TEAM>` and skip name matching for them entirely.

### 2.3 Providers

```python
class Provider(Protocol):
    name: str
    def fetch(self, season: int, week: int | None) -> Iterable[RawProjection]: ...
```

`RawProjection` is `(source_id, source_name, pos, team, stats: dict[str, float])`. The adapter's entire job is producing that. It does not resolve player IDs (the crosswalk does), does not apply scoring (the blend does), and does not know what a league is.

Start with two. `espn_proj` pulls ESPN's own projections through the same client phase 1 already uses. `own_model` reads whatever you're generating, even if version one is a CSV you hand-build. Having a second real source from day one is what proves the N-source design actually works, and a stub that returns nothing proves nothing.

`yahoo_proj` is a third if Yahoo exposes usable projections for your league; treat it as optional.

### 2.4 Scoring and blend

`scoring.py` is a pure function: `(stat_line, league_rules) -> points`. No I/O, no platform knowledge. Pull `league_rules` from `league_scoring`, which phase 1's `get_league_settings` already knows how to fetch.

`blend.py` combines N normalized providers per player. Weights live in `config/weights.yaml`, not code:

```yaml
default:
  espn: 1.0
  own:  1.0
missing_source: renormalize   # drop it and rescale the rest
```

Start equal-weighted. Per-source weighting is guesswork until you have data, and you'll have that data by midseason from the `actual` table. Then output tiers by clustering the blended points within position (gap-based works fine, no need for k-means), and write the compact results the MCP layer will read:

```sql
CREATE TABLE value_board (
  league TEXT, season INT, week INT, player_id TEXT,
  blended_pts REAL, tier INT, rank_pos INT, rank_overall INT,
  sources_used INT,
  PRIMARY KEY (league, season, week, player_id)
);
```

Then add three tools that read only this table, no live calls: `get_value_board(league, position, limit)`, `get_start_sit(league, week)`, `get_waiver_targets(league, position, limit)`. These are the payoff. They're fast, they're small, and they're precomputed.

### 2.5 Weekly actuals

Once the season starts, a Tuesday job writes real stat lines into `actual`. Costs nothing now and is the only way you'll ever get accuracy-weighted blending later. Do it from week one or you'll wish you had.

---

## Phase 3 — scheduling

launchd, not cron, since the mini sleeps and launchd catches up on missed runs.

```
combine.projections   Wed 04:00        full pipeline: fetch, crosswalk, blend
combine.injuries      daily 07:00      injury/status refresh, rebuild value_board
combine.actuals       Tue 06:00        write last week's actuals
combine.health        daily 07:30      ESPN cookie check, alert on failure
combine.gameday       Sun 09:00,11:30  inactive-player check in your lineups
```

Every job writes a row to a `run_log` table with status and duration. When something's stale you want to know whether the job failed or never fired.

---

## Phase 4 — PFF, when the API ships

One adapter in `providers/pff.py`, its aliases added to the crosswalk, one line in `weights.yaml`. Write it against the real field names, not the documentation's examples. Keep the raw data in `projection` and never expose PFF's numbers as their own values through any tool; the blend output is yours, the inputs are theirs.

If that turns out to be more than a day of work, something upstream is wrong and it's worth fixing then rather than at source five.

---

## Phase 5 — Discord bot, stretch

Separate process on the mini, reads `combine.db`, never touches ESPN or Yahoo directly. Push only: inactive starters before kickoff, waiver deadline reminder, Tuesday digest of value board movement. Routine summaries go through a small model, and you only reach for the strong one when a message actually needs analysis. Interactive querying stays on the connector where it's covered by your subscription.

---

## Start here

Phase 0 and 1.1 through 1.4 in one sitting gets you a local server you can point Claude Desktop at over stdio for testing, before any tunnel or WAF exists. Prove the tools return the right shape locally, then do 1.5 through 1.7 as one deployment step.

Tell me which piece you want to write first and I'll build it.
