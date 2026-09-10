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

## The app

```bash
uv run streamlit run app.py
```

Two modes, picked at the top of the sidebar.

**Week** is the in-season view and the default. Your matchup, both projected
totals, starters and bench with a PFF role line on each, anyone hurt or on bye
who is still in your lineup, and the same start/sit calls the CLI gives you.
If the PFF crosswalk has never been built it degrades to the projection alone
and tells you which command fixes it. The week selector defaults to whatever week the league says it is; set a
number to look back.

**Draft** is the old page and is dormant until next August: needs at the top,
timing against your next pick in the middle, the full board below, player
detail at the bottom. Draft slot and pick-on-the-clock appear in the sidebar
only in this mode.

Auto refresh follows the mode, on for Draft and off for Week, and polls every 30s, with a pause toggle and a
Refresh now button that clears the cache. Data is cached for 25s so clicking
around costs nothing; one ESPN round trip feeds every section on the page.

Rows are coloured: red for `OUT?` and `NEWS!`, green for `VALUE`, dimmed for
`no-pff`. Every column is sortable, so the ASCII tables below are the same
data without the mouse.

The CLI still works and is documented below. The app is a front end over the
identical code, not a reimplementation.

## The Discord bot

The in-season interface from anywhere. Slash commands for asking, and a daily
morning check that stays silent unless there is something to act on.

```bash
uv run combine bot                 # foreground, for testing
./scripts/install_agents.sh        # background, survives reboots
```

The installer fills in the repo path and the absolute path to `uv`, creates
`logs/`, and loads the agent. Both substitutions matter because launchd is not a
shell: it starts with a minimal PATH and no working directory, so `uv run` alone
fails with command not found and the job dies at boot with nothing obvious in
the logs. Run it again after pulling; it reloads rather than duplicating.

`--with-app` also runs the Streamlit app in the background, same low priority,
same survives-a-reboot. It binds `127.0.0.1` by default, which is what the
Cloudflare tunnel wants, so it is reachable only on the mini itself. To read it
from another machine without setting up the tunnel:

```bash
./scripts/install_agents.sh --with-app --bind 0.0.0.0
```

Then browse to the mini's address on port 8501, including over Tailscale. The
app has no login of its own and every page load acts as your ESPN session, so
anything that can reach it can read your rosters and make requests as you. On a
home network that is usually a fine trade. The version that actually
authenticates is `127.0.0.1` plus the tunnel with an Access policy.

`--uninstall` removes both agents.

**After every pull, restart what is running.** Both agents hold code in memory
and neither notices a `git pull`:

```bash
git pull && ./scripts/install_agents.sh          # add --with-app if installed
```

The bot needs it because a new or changed slash command only registers when the
process starts. The app needs it because Streamlit re-executes `app.py` on change
but keeps already-imported modules, so a new module is missing and a changed one
is stale. That is the same mechanism behind the `Band` dataclass error further
down this file, and restarting is the fix in both directions.

`launchctl kickstart -k gui/$(id -u)/com.thecombine.bot` restarts just the bot if
you would rather not re-run the installer.

Commands: `/week`, `/startsit`, `/waivers`, `/scoreboard`, `/compare`,
`/glossary`, `/health`, `/clear`. `/week`, `/startsit` and `/waivers` take an optional league and cover all of them when you leave it
blank, which is usually what you want on a Sunday. Three leagues takes about
five seconds. All commands answer to the owner only, because otherwise anyone who can see the bot can read
your rosters and cause ESPN requests authenticated as you.

The scheduled check runs at 08:30 Pacific every morning, not just Sunday. Games
are played Thursday through Monday, so a Sunday-only check misses a Thursday
injury and every waiver window that opens midweek.

Daily only works because the same report does not go out twice. The check
remembers what it last said per league, by which players and which swaps rather
than by the numbers, so a starter who is out for the season is news once instead
of six mornings running. ESPN nudges projections through the day, and matching on
the rendered text would have made every morning look like fresh news. State lives
in `data/last_post.json`; delete it to make the next check post again.

Output is embeds: the coloured left bar, a title, columns for the numbers, a
footer for the caveats. The colour is severity rather than decoration. Green
means nothing to do, blue means here is your information, amber means a decision
is waiting, red means someone who cannot play is in your lineup, grey means a
league that cannot answer. It reads from the notification shade without opening
anything.

Tables stay in code blocks inside the embed, because embeds are not monospace and
alignment is the whole point of a table.

Under `/week`, `/startsit`, `/waivers` and `/scoreboard` there are three buttons:
the previous week, Refresh, and the next week. They edit the message in place
rather than posting a new one. The buttons keep working after the bot restarts,
which is worth knowing because most bots' buttons do not: the state lives in the
button's own id rather than in the process that sent it.

`/clear` deletes messages in the channel it is run in, and it is the only
command in this repo that destroys anything. Read-only is about ESPN and Yahoo,
where a write would be a real roster move; clearing the bot's own status posts
out of its own channel is a different thing. It still dry runs by default and
reports what it would delete, and only deletes when you pass `confirm: True`.

It needs **Manage Messages** and **Read Message History** on that channel. Grant
those in Discord, not in the developer portal: the portal's permission
checkboxes only build the invite URL and changing them does nothing to a bot
already in the server. Right-click the channel, Edit Channel, Permissions, add
the bot.

Discord bulk-deletes in one request but only for messages under 14 days old.
Anything older goes one at a time at about one a second, so clearing a channel
with months of history takes minutes. The dry run counts how many fall in that
bucket and tells you roughly how long to expect.

Chosen over exposing the app through a tunnel because of the direction of the
connection. The bot dials out to Discord and holds a websocket, so there is no
public hostname, no ingress rule, no Access policy to keep correct and no inbound
surface at all.

Testing the scheduled check without waiting for the morning:

```bash
uv run combine notify --dry-run          # print what it would post, send nothing
uv run combine notify                    # exactly what the schedule does
uv run combine notify rcl --force        # post even though nothing is wrong
```

`--force` exists because the check's success condition is silence, which is
indistinguishable from the whole thing being broken. It labels the post as a
manual test so a forced message never reads as a real recommendation. It sends
over REST without joining the gateway, so it does not conflict with the running
agent; two gateway sessions would mean two copies of the bot answering every
slash command twice.

Setup lives in `.env`: `DISCORD_TOKEN` plus `DISCORD_GUILD_ID`,
`DISCORD_CHANNEL_ID` and `DISCORD_OWNER_ID`. The token is a secret and belongs
nowhere else. The IDs come from right-click Copy ID with Developer Mode on.

Two things that will bite:

The bot needs Send Messages **in the target channel**, not just at the server
level. A channel permission override silently blocks the scheduled post while slash
commands keep working, because interaction replies go through a webhook and
ignore channel send permissions. `/health` will look fine while the schedule
posts nowhere.

Tables are re-rendered narrow for Discord, about 48 characters, because Discord
wraps code blocks rather than scrolling them. The terminal renderers run to 130
characters and are unreadable on a phone. Prose stays as markdown so Discord can
wrap it.

## Reaching it from a phone

The app runs on the Mac mini, which is always on and is also the Plex server.
Access goes through the Cloudflare tunnel already running there for the *arr
stack, so this is one more ingress rule rather than new infrastructure.

```yaml
# ~/.cloudflared/config.yml, alongside the existing *arr entries
ingress:
  - hostname: combine.example.com
    service: http://localhost:8501
  # ... existing rules, catch-all stays last
```

**Put a Cloudflare Access policy on that hostname.** The app has no login of its
own and every page load acts as your ESPN session, so a tunnel without Access is
a public URL that drives your ESPN account. Email OTP or Google, same as the
*arr stack.

Then run it as a launchd agent so it survives reboots and never competes with
Plex:

```bash
./scripts/install_agents.sh --with-app
```

It launches under `taskpolicy -b`, macOS background QoS, so any Plex transcode
wins CPU and I/O contention and the app yields instead of competing. Measured
cost when idle is a Python process with pandas loaded and effectively no CPU; the
outcome history it reads builds in about a second and occupies 4.4MB. Auto
refresh is off by default in Week mode, so it is not polling ESPN for nobody.

Two gotchas worth knowing before you debug the wrong thing:

Streamlit is a websocket app. Behind a proxy without `--server.enableCORS false
--server.enableXsrfProtection false` it serves the page and then hangs forever on
"Please wait...", which reads as a tunnel fault and is not. The plist sets both,
which is safe only because Access is doing the authentication.

**Do not put Access on the hostname you later use for the MCP server.** Build
guide item 9 covers this: Access bounces Anthropic's connector with a login
redirect and fails with a useless error. The browser app wants Access, the MCP
endpoint wants its own hostname with no Access policy and the bearer token doing
the work.

Tailscale is the alternative if you would rather not rely on Access: bind
`0.0.0.0` instead and the app is never publicly routable. It needs the client on
every device you use.

Hosted options were rejected for two concrete reasons. `data/` is gitignored and
holds the 33MB outcome database, the PFF caches, the projection exports and the
id crosswalk, so a deploy from the repo would silently lose the outcome columns,
the Role column and the scaling comparison threshold. And it would mean putting
ESPN session cookies in a third party's secret store.

## Scoreboard

```bash
uv run combine scoreboard          # every league, live
uv run combine scoreboard 3        # a past week
```

`*` marks your matchup, `F` is final, `>` in progress, `-` not started. PROJ is
ESPN's projected final, which moves during games, so it is labelled as theirs
rather than presented as ours. LFT is starters whose game has not ended, which
is there because a 20 point lead with nine players left is not a lead.

The same view is `/scoreboard` in Discord, narrowed for a phone, and a Scores
mode in the app where auto refresh defaults to on, since this is the one view
whose numbers really are moving.

The Yahoo league cannot appear. A scoreboard needs the opponent's lineup and the
hand-entered league has none, so it shows as unavailable with the reason rather
than silently going missing. It joins when the API is approved.

## Waiver wire

```bash
uv run combine waivers              # every league, this week
uv run combine waivers rcl 3        # one league, a specific week
```

Free agents who would improve this week's lineup. The number is what the whole
lineup is worth afterwards rather than a head to head, so a cascade counts: an
add that only helps because he frees a flex spot is still an upgrade, and one
who beats a starter you would not have started anyway is not.

Two columns, on purpose. WEEK is the calibrated gain, so a position ESPN
systematically over-projects is marked down before the comparison. SEASON is
what the drop costs or gains for the rest of the year, kept in its own column
because a week is not worth a season, and one blended number would hide which of
the two you are trading. Both are signed, so a move that wins Sunday and costs
you November reads as `+1.7 / -12.8` rather than as a recommendation.

Each row is an alternative, not a sequence. Every one is scored against the
lineup you have right now, which is why three rows can all name the same drop.

A row marked `*` (⚠️ in the app) only clears the bar because of the calibration,
and the note underneath says how many player-weeks that correction rests on. DT is the live
example: +2.49 measured on 40 observations is a real finding on a thin sample,
and you should see the sample rather than a confident number.

Also `/waivers` in Discord, with an optional league, and a Waiver wire section at
the bottom of the app's Week page. The scheduled check only pings you about an add
that does not cost season value; one that buys a week and pays for it later
waits until you go looking, which is what `/waivers` is for.

The Yahoo league cannot answer this. Free agents need the API, so it reports the
reason rather than an empty list. Kickers and team defenses are not covered in
any league yet.

## The week (CLI)

```bash
uv run combine week dmwd        # current week
uv run combine week rcl 3       # a specific week
```

Starters in the league's own slot order, bench sorted by projection, then two
call-outs: starters who are hurt or on bye, and bench players who outproject a
starter they are slot eligible for.

That second list is not a start/sit recommendation. It compares ESPN's weekly
projection and nothing else, one for one, ignoring edges under a point. The
real call, blended projections plus PFF usage and efficiency, arrives with the
PFF API client. Treat it as a "look at this" list, not an answer.

Projections come off the box score, which is the only place weekly numbers
exist. Before week 1 kicks off every actual reads 0, which is correct rather
than broken.

## Role numbers and which season they are

Every role line is tagged with the season and how many games are charted, like
`2025 17g`. Early in a year most rows are last season on purpose: two games of
this season is a rate built on two games, so the tool keeps last year until a
player has enough games that it would stop warning about the sample.

All of these are regular season only. PFF's season totals quietly include
preseason and playoff snaps, which is not cosmetic: Drake Maye's 2025 total is
23 games and 770 dropbacks against a real regular season of 17 and 601, and his
passing grade reads 75.2 instead of 87.8.

## PFF ids

```bash
uv run combine pffids rcl
uv run combine pffids dmwd
```

Resolves your league's players onto PFF's stable `player_id` and stores the
mapping in `data/crosswalk_pff_ids.csv`. Run it once, and again when rosters
churn. Everything that reads PFF usage or efficiency joins on that id instead
of re-matching names on every call.

Both leagues resolve at 100%. It prints how each player was matched, and the
only expected miss is D/ST, because PFF has no team-defense entity. A player it
genuinely cannot place lands in `data/unmatched_pff_ids_<league>.csv` for you
to resolve by hand in `config/crosswalk_overrides.csv`.

The file merges across leagues, since ESPN player ids are global. `data/` is
gitignored, so a fresh clone rebuilds it with those two commands.

Before kickoff this reads last season's PFF data on purpose. A season that has
not started still returns rows and they are preseason camp snaps, so the client
asks PFF where the calendar is and says which season it used.

## Start/sit (CLI)

```bash
uv run combine startsit              # every league
uv run combine startsit rcl
uv run combine startsit dmwd 3
uv run combine compare dmwd "Nabers" "Golden"
```

`startsit` leads with the optimal lineup when yours is not it. That check is
exact slot assignment on ESPN's projections, no prediction involved, and in the
2025 backtest it was worth +3.5pp of win rate and +2.5 points a week against
lineups as actually fielded. It respects eligibility, treats a ruled-out
starter as worth zero, and will not suggest moving anyone whose game has
kicked off.

Below that it prints only the slots with a real question. "Real" is measured
rather than guessed: across 174,384 comparable pairs in 2025, the higher
projection actually outscored the other player 51% of the time at a gap under
half a point, 54% at a gap of one to one and a half, and 58% at two to three.
So a flat one-point threshold surfaces coin flips and trains you to ignore the
flags.

The gap alone is also the wrong unit. Two points means more between two
defenders, whose outcomes scatter with a standard deviation around 6, than
between two backs projected 20+, where it is 10.6. So the threshold is the gap
divided by the spread of the two players' outcome distributions, which lines
the accuracy curve up across positions where the raw gap does not, and the cut
sits at 0.25 of a standard deviation. In practice that is about 1.2 points
between two low-projected defenders and 2.6 between two big backs. Pairs that
clear it are right about 57 to 59% of the time.

With no stored outcome history it falls back to a flat point, which is honest
rather than degraded: with nothing measured, a picked constant is all there is.

The questions it prints: a bench player
outprojecting a starter he can legally replace, and anyone who cannot play and
is still in your lineup. A lineup that is already right gets one line saying
so. That is the intended output, not a failure.

Each call shows both players with their PFF role line, the projection gap, and
the usage gap, then a verdict:

* **SWAP** — the projection and the usage agree, and the gap is 3+ points.
* **LEAN** — they agree but the gap is small, or there is no usage to check.
* **COIN FLIP** — they disagree. That is the finding. The two are not averaged
  into one number, because averaging a points projection with a grade produces
  something that means nothing.

When nothing qualifies it says so and then shows the closest comparisons it
rejected, phrased as how many more points the bench player would need. That is
deliberately not a signed gap next to a threshold: a bench player 1.6 behind a
starter who needs to be 1.6 ahead is 3.2 short, and printing "-1.6" beside
"1.6" reads as a match.

`compare` does the same for any two players in the week's matchup, whether or
not they are a legal swap for each other.

Opportunities only compare within a position family. A tight end's targets and
a running back's touches are different units, so across positions the tool says
so and falls back to the projection alone.

Before kickoff the usage is last season's, which every output labels as a prior.
Both commands need the id crosswalk, so run `combine pffids <league>` first.

## Training data and baselines

```bash
uv run combine train build          # pull last season into SQLite, resumable
uv run combine train status         # what is stored
uv run combine train baseline       # the bar a model has to beat
```

`build` pulls every rostered player-week from a past season: ESPN's weekly
projection and the actual score, already under your league's rules, plus PFF's
charted stat line for the same weeks. 2025 gives 7752 player-weeks. It skips
whatever is already stored, so it is safe to re-run after an interruption.

`baseline` scores ESPN and two no-model predictors on two metrics. MAE is how
close the number is. Pairwise accuracy is how often the player you were told to
prefer actually outscored the other, and the `close` column restricts that to
pairs within 3 projected points, which is where the real decisions are.

ESPN currently sits at MAE 5.67 and 55.1% on close calls. Any model has to beat
both out of sample or it does not ship.

```bash
uv run combine train model      # fit the residual model and score it honestly
uv run combine train backtest   # replay a season: optimizer and posture
```

`backtest` replays a season with real matchups, changing only your side and
only with legal moves. It is what established that the optimizer is worth
+3.5pp of win rate, and that posture (ranking by ceiling when projected to lose)
loses at every threshold and so is not wired in.

`model` currently prints a rejection. A ridge model on the residual was built and
held out properly, and when it overrules ESPN on a close call it is right 47.5%
of the time against ESPN's 55.6%. The command stays because the harness is
reusable and because the flip test it prints is the standard any future model
has to clear.

## What the columns mean

```bash
uv run combine glossary
```

The same glossary is an expander under the tables in the app's Week mode. Role
is PFF usage and efficiency shown beside the projection and never blended into
it, because grades and rates are on scales that have nothing to do with fantasy
points. Floor, ceiling, boom and bust describe the spread around a projection,
which ESPN does not give you: a projection is a mean, and in 2025 the median
outcome landed 1.4 points below it.

Those outcome columns are context for a close call, not a ranking. Sorting a
lineup by ceiling or floor was backtested and lost at every threshold, so the
tool shows them and leaves the judgement to you.

## Draft day (CLI)

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
    #  POS   PLAYER               TM   ESPN    PFF    AVG   VORP   ADP  VAL  TD% BYE TIER  BUZZ FLAG
   #1  RB1   Jahmyr Gibbs         DET 365.7  342.9  354.3  185.2   1.3   +0  24%   6 T1      +2
   #7  WR3   Some Guy             MIA 210.4  248.1  229.2   96.4  41.2  +34  41%   9 T3   SPLIT VALUE+34
  #14  RB6   Another Guy          NYG 240.1      -  240.1   71.0  64.1  +50   -   11 T3      -1 NEWS! 15g
```

| col | what it is | how to use it |
|-----|-----------|---------------|
| `#` | Overall rank by `VORP` across the whole pool. Does not shift when you filter. | Compare across positions. |
| `POS` | Position plus his rank at it, e.g. `RB4` = fourth best RB available. Also whole-pool. | Compare within a position. |
| `PLAYER` | ESPN's name, truncated at 21 chars. | The PFF row it matched may be spelled differently; `try crosswalk` shows pairs. |
| `TM` | NFL team, ESPN's spelling. | PFF writes some differently (`HST`, `ARZ`, `LA`). Handled internally. |
| `ESPN` | ESPN's projected season points, **scored under this league's rules**. | One opinion. Do not read alone. |
| `PFF` | PFF's projected season points, also scored under this league's rules. Dash means no match. | Second opinion. Two sources agreeing is weak evidence; disagreeing is the useful part. |
| `AVG` | Plain mean of the projection sources (`ESPN`, `PFF`, and whatever gets added later). Falls back to whichever exist. | Raw projected points. Comparable *within* a position, misleading across them. Does **not** set the order. |
| `VORP` | Value over replacement. `AVG` minus replacement level at his position, where replacement is the last player who starts somewhere in a 12-team league. | **This sets the order.** It is what makes a QB and an RB comparable. See below. |
| `ADP` | PFF's average draft position, from the rankings export **matching this league's scoring format**. Dash means unknown. | Where the room takes him. Also tells you roughly whether he survives to your next pick. |
| `VAL` | `ADP` minus his overall `VORP` rank. Positive = the room takes him later than the numbers say he is worth. | **Who** is underpriced, never **when** to take him. A big `+` often means you can wait, see below. |
| `TD%` | Share of his projection that comes from touchdowns, using **this league's** points per TD. Dash means no PFF match. | Volume projects reliably, touchdowns do not. Two players at the same `VORP` are not the same bet if one is at 20% and the other at 45%. Over ~35% is volatile and the first candidate for negative regression. |
| `BYE` | Bye week. | Late rounds, avoid stacking your starters on one week. |
| `TIER` | Tier **within his position**, computed by me, not by PFF. Breaks where the drop in `VORP` to the next player at that position is more than 1.6x the typical drop. | A tier edge is the "take him now or wait a round" line. Within a tier, take the best `VAL`. Whole-pool, so it does not change when you filter. |
| `BUZZ` | Net analyst sentiment from the opinion lists, or `SPLIT` when they contradict each other. Blank = nobody mentioned him. | Never in the blend. `SPLIT` is the interesting one; `try notes` gives the detail. |
| `FLAG` | The single most important thing about the row. See below. | Read this before the numbers. |

**How `VORP` works.** *Value Over Replacement Player*, borrowed from baseball. It answers one question: *how much better is this player
than what I could get at his position anyway?*

Twelve teams start one QB each. There are far more than twelve usable QBs, so
if you skip the best one you still end up with a fine one. Twelve teams start
two RBs plus a flex, and backs run out fast, so skipping the best one leaves
you somewhere much worse. Raw points cannot see that difference. `VORP` is
built to.

**Replacement level** is the last player at a position who still starts
somewhere in the league. It comes from your league's own settings: slot count
times team count, with flex slots split across the positions eligible for
them. DMWD has one RB/WR/TE flex, which adds 12 jobs spread three ways, so RB
replacement sits at 24 + 4 = **28th best RB**, not 24th.

`VORP` = `AVG` minus that replacement player's points.

Worked, from DMWD's real numbers:

| pos | replacement is | best player | his `AVG` | his `VORP` |
|-----|----------------|-------------|-----------|-----------|
| QB  | 12th best, ~301 | Josh Allen  | 348.0 | **47** |
| RB  | 28th best, ~181 | Jahmyr Gibbs | 342.9 | **161** |
| WR  | 28th best, ~213 | Puka Nacua  | 323.6 | **111** |
| TE  | 16th best, ~154 | Trey McBride | 245.9 | **92** |
| K   | 12th best, ~119 | best kicker | 123.5 | **5** |

Josh Allen outscores Gibbs on raw points and is worth about a third as much,
because skipping Allen costs you 47 points and skipping Gibbs costs you 161.
The kicker row is the same logic at its limit: the best kicker alive is worth
five points more than one you can take in the last round. That is why nobody
drafts a kicker early, and `VORP` says it as a number instead of as folklore.

**Why this had to exist.** `VAL` compares `ADP`, a draft-order number, against
our rank. Ranking on `AVG` meant ranking on raw points, and in RCL the pool is
full of linebackers projected for 230 and quarterbacks over 300 that nobody
drafts early. They pushed every running back down the board and made **every**
`VAL` negative. If `VAL` ever goes systematically negative again, that is the
symptom of this same class of bug.

**Reading it.** Use `AVG` to compare two players at the same position, since
they share a replacement level and the subtraction cancels. Use `VORP` the
moment you compare across positions. Within one position the two give
identical orderings, which is also why positional tiers are unaffected by the
choice.

**What it assumes.** That every team fills every starting slot, that starters
are what matter (bench depth is not counted), and that flex demand splits
evenly across eligible positions. All three are standard simplifications and
all three are approximations.

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
| `15g` | Not a flag but a marker after it: PFF projects him for fewer than 17 games. | An absence is already priced into that season total, so the projection is not being optimistic. Worth knowing why. |

**Where columns go blank, and why.** `ADP` and `VAL` are always dashes for
RCL defenders, because PFF publishes no IDP draft position anywhere. RCL is
also a keeper league, so two players per team are gone in ways public ADP
cannot know; treat RCL `VAL` as a hint, not a number. The kept players
themselves are correctly excluded from the pool by ESPN, so the board is not
offering you undraftable people. The half-PPR export
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
     #  POS   PLAYER                TM    VORP   ADP  VAL  FLAG
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

Within each group, rows are ordered by `VORP`, best player first, **not** by
`VAL`. Everyone in GONE is someone you cannot wait on, so the cost of waiting
is already zero for all of them and the only question left is who is best.
`VAL` chooses between groups; `VORP` orders within them. Use `VAL` as a
tiebreaker when two players are close in `VORP`, and ignore it when they are
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

**From round 4 on, use this instead of the board.** Rounds 1-3 are
best-player-available. After that a fourth RB is worth less than a first TE no
matter what the numbers say, because you can only start so many.

```bash
uv run combine try needs dmwd
uv run combine try needs rcl 15     # trailing number = how many rows
```

Reads your **live** roster from ESPN, so it updates as you draft. It shows:

- which starting slots are still empty, and which positions can fill them
- your current starters by slot, and who is on the bench
- a **BYE PILEUP** warning when 3+ of your starters share a bye week
- the best available players **only at positions you still need**

Before your first pick it will say every slot is empty, which is correct but
useless; use the board until you have picks. Slot filling is greedy, dedicated
slots before flex, so it can be marginally suboptimal in odd cases but will
never claim a slot is filled when it is not.

**Look up one player.** News, analyst opinion, TD share and his board line.

```bash
uv run combine try notes dmwd Kittle
uv run combine try notes rcl "Byron Young"
```

Use it whenever a row shows `NEWS!`, `OUT?`, `SPLIT`, or before spending an
early pick on someone.

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
uv run combine try needs dmwd   # roster-aware, use from round 4 on
uv run combine try roster dmwd # season roster, no weekly numbers
uv run combine week dmwd       # the in-season view, weekly numbers
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
