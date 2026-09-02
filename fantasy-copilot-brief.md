# Project brief: fantasy football co-owner setup

## What we're building

A read-only "co-owner" setup that gives Claude live access to my fantasy football leagues plus a blended projection model, so I can query everything from Claude on my Mac, MacBook, and phone. Claude reads and advises. I make every roster move myself. Nothing writes back to the platforms.

I run three leagues: two on ESPN, one on Yahoo. I want this for draft prep and weekly in-season decisions (start/sit, waivers, trades, bye planning).

Status note: the PFF Pro API is announced but not released yet ("coming soon"). We're building without it for now. Design the projection layer to take N sources so PFF slots in as one more provider when it ships, with no rework. Starting sources are the platform projections and my own model.

## The two layers

The design splits by job:

1. Live league state comes from MCP servers wrapping the ESPN and Yahoo fantasy APIs (rosters, matchups, free agents, injuries, league settings).
2. Valuation comes from a pipeline script that normalizes projections against each league's scoring rules and blends them into composite tiers and values. Starting sources are the platform projections and my own model. PFF Pro joins as a third source once its API releases.

Claude reads both layers, so it reasons over live state and blended numbers at the same time.

## Projection provider model (design principle)

The valuation layer is built so any number of projection sources can plug in, not two-plus-PFF. The system should not care whether it's fed 2 sources or 9. This rests on three pieces:

- Common normalized format. Every source is translated into one internal shape. A "provider" is a small adapter whose only job is to take a source's raw output (its own player names, stat categories, scoring assumptions) and emit that normalized shape. Adding a source = writing one adapter. Nothing downstream changes.
- Player crosswalk as the universal key. Every provider resolves its players to one canonical player ID, so the same player from every source collapses to one row. This is the join that makes cross-source comparison possible. Build it to be corrected by hand: automated matching plus a manual override file for the cases that don't auto-match (rookies, name spellings, duplicate names, team changes). It will need correcting.
- Scoring normalization. Providers emit underlying stat lines, not final point totals. The blend applies each league's scoring rules to those stats. This is what lets one adapter serve all three leagues instead of writing per-league adapters.

With those in place, the blend just combines N normalized projections per player, and weighting is config, not code: equal weight, per-source weight, drop-a-source-if-missing, or eventually weight by measured accuracy. Future hook: with a canonical schema we can log projected vs actual over the season and let the data pick the weights.

## Components to build

1. MCP servers
   - ESPN server registered against both ESPN league IDs (the `espn-api` Python wrapper instantiates per league ID, so two configured leagues, not two servers).
   - Yahoo server for the third league (these usually auto-discover all leagues on the account after OAuth).
   - stdio is fine. We wrap each stdio server in a transport gateway (supergateway or mcp-proxy, both run in Docker) that exposes it as streamable HTTP. So pick the best forks on features, not on whether they natively speak HTTP.
   - Read-only. Do not configure write/transaction tools.
   - Keep tool outputs lean (see the token-efficiency section). Tools should return small, decision-ready answers, not raw full-league dumps.

2. Automation pipeline (script + scheduler)
   - Pull each league's scoring and roster settings.
   - Implement the projection provider model above. Start with two providers: the platform projections and my own model. ESPN and Yahoo live state also feeds this.
   - Build the player crosswalk (canonical IDs + manual override file). This is the main engineering task.
   - Blend N normalized providers into composite values/tiers, with weighting held as config.
   - Write output to a local store (SQLite or JSON) that the MCP layer and any future dashboard/bot all read from.
   - Schedule it: projections weekly, injuries and availability daily during the season (cron or launchd).
   - Adding any future source (PFF when its API releases, or anything else): write one adapter, extend the crosswalk, add a weight. No rewrite.

3. Remote reachability + security
   - Expose the gateway HTTP endpoints over Cloudflare Tunnel (already in use for my *arr stack, so this is another named route).
   - Register them as native Claude custom connectors so claude.ai web and the iOS/Android apps can all use them. Claude connects from Anthropic's cloud, not my local device, so the endpoint must be publicly reachable from Anthropic's IP ranges. A "only my email" Cloudflare Access gate would block Anthropic and must not be used as-is.
   - Secure it one of two ways (help me decide): OAuth on the MCP server itself (the connector config takes a client ID/secret and runs the flow), or a bearer token plus an IP allowlist for Anthropic's published ranges at Cloudflare.

4. Stretch goal: Discord bot for notifications
   - Push alerts I'd otherwise have to ask for: inactive players in my lineups before kickoff, waiver reminders, a weekly digest of blended values.
   - Runs on the mini, calls the Claude API server-side, reads the same local store. Interactive querying stays on the native connectors; the bot is just for push.

## Environment (what I already have)

- Mac mini running 24/7 (Plex + *arr stack, Docker in use, comfortable running services).
- Cloudflare Tunnel already configured.
- PFF Pro subscription (API "coming soon", not released yet, so deferred).
- Private GitHub repo for all code. No secrets committed.
- Windows PC available but the mini is the host.

## Token / usage efficiency (important)

Interactive querying runs through the native Claude apps on my subscription, so the cost is my plan's usage limits, not a metered API bill. The way to blow through those limits is fat tool outputs bloating the context on every turn. Design against that:

- The pipeline pre-computes compact, decision-ready outputs into the local store (my roster with blended values, top-N waiver targets, this week's matchup). MCP tools read that digested store and return small structured answers, not raw 300-player JSON.
- Scope the tools to specific questions with capped, parameterized outputs (`get_my_roster`, `get_top_waivers(position, limit)`, `get_matchup`), instead of one `get_everything` tool.
- Filter server-side (e.g. ESPN's `X-Fantasy-Filter`) so only relevant slices come back.
- Query one league at a time by default, not all three at once.
- Lean tool design pays off twice: it protects my plan usage now, and if I build the Discord bot later (which does use metered API tokens), it keeps that bill down too. For the bot, also consider a cheaper model for routine notification summaries and reserve the strong model for analysis.

## Constraints and gotchas to handle

- Secrets live in env only, never in the repo. `.gitignore` the env file.
- ESPN `espn_s2`/`SWID` cookies and Yahoo OAuth tokens expire, ESPN's especially will die mid-season. Plan for refresh.
- ESPN's API is unofficial and can break on their changes. Yahoo's needs a registered developer app and is read-focused, which is fine since we're read-only.
- When PFF's API lands, design its provider against the real field names, not guessed. Keep PFF data inside my own private model. Fine for personal use, don't redistribute their raw projections.

## Open decisions I want help working through

- Which community ESPN and Yahoo MCP forks are best on features (transport no longer matters, the gateway handles it).
- OAuth-on-server vs bearer-token-plus-IP-allowlist for securing the connector endpoints.
- Local store: SQLite vs flat JSON.
- Projection blend weighting across sources (equal, per-source, or accuracy-weighted later).

## Suggested build order

1. Stand up the ESPN and Yahoo MCP servers, wrap them in the transport gateway, expose over the tunnel, register as connectors. This gives live league access immediately, even before the pipeline exists.
2. Build the crosswalk and normalization script for the two starting sources.
3. Layer the blend logic into composite values/tiers (written to take N sources).
4. When the PFF Pro API releases: add it as a provider, extend the crosswalk, add it to the blend.
5. Stretch: Discord notification bot.

Steps 1 and 2 are independent, so we can start with whichever I want working first.
