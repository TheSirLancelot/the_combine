"""FastMCP server. Streamable HTTP, bearer-authenticated, bound to loopback.

Tool design rules, from the brief:
  one league per call, every list capped server-side, compact text out.
"""

from __future__ import annotations

import hmac

from fastmcp import FastMCP

from .config import BEARER_TOKEN, HOST, PATH, PORT, get_league, leagues
from .format import ranked_table, roster_table
from .pipeline.board import build as build_board, filter_pos, render as render_board
from .pipeline.draftplan import next_pick, partition, snake_picks
from .platforms import client_for

mcp = FastMCP("combine")

MAX_LIMIT = 40


@mcp.tool
def list_leagues() -> str:
    """The league slugs every other tool takes. Call this first if unsure."""
    return "\n".join(
        f"{slug:6s} {c.platform:6s} {c.name}" for slug, c in leagues().items()
    ) or "none configured"


@mcp.tool
def get_league_settings(league: str) -> str:
    """Scoring rules and starting lineup slots for one league. Needed before
    giving positional advice, since these leagues differ a lot (one is IDP with
    no kicker, the other is full PPR with K and D/ST)."""
    c = client_for(league)
    cfg = get_league(league)
    labels = c.scoring_labels()
    slots = ", ".join(f"{k} x{v}" for k, v in c.roster_slots().items())
    scoring = ", ".join(
        f"{labels[i]}={p:g}" for i, p in sorted(c.scoring_rules().items()) if p
    )
    return f"{cfg.name} ({league})\nstarters: {slots}\nscoring: {scoring}"


@mcp.tool
def get_draft_board(league: str, position: str = "", limit: int = 20) -> str:
    """Best available players for one league with BOTH projection sources side
    by side: ESPN's and PFF's, each already scored under this league's rules,
    plus AVG, VOR, tiers, ADP and VAL. This is the drafting tool. VAL is ADP minus our overall rank: positive means the room is
    letting him fall past where the numbers put him. FLAG calls out OUT?
    (ranked and drafted early but projected zero, so something happened),
    no-pff, VALUE/REACH, and source disagreement. Optional position filter."""
    limit = max(1, min(limit, MAX_LIMIT))
    c = client_for(league)
    # Unfiltered pool: overall_rank and the ADP comparison are only meaningful
    # against the whole board, so filter for display, not at the source.
    pool = c.free_agents(position=None, limit=250)
    rows, counts = build_board(league, pool, c.roster_slots(), c.team_count())
    shown = filter_pos(rows, position)[:limit]
    label = f"{get_league(league).name} board{f' at {position}' if position else ''}"
    unmatched = counts.get("unmatched", 0) + counts.get("ambiguous", 0)
    note = f"\n({unmatched} of {len(pool)} had no PFF match)" if unmatched else ""
    return render_board(shown, label) + note


@mcp.tool
def get_draft_plan(league: str, on_clock: int, slot: int = 0, position: str = "",
                   limit: int = 12) -> str:
    """Snake-draft timing. Given your draft slot and the pick number currently
    on the clock, splits the best available players into who will be GONE
    before your next turn, who is a COIN FLIP, and who will still be THERE.

    Use this to decide WHEN. Take the best VAL among the GONE group, because
    those are the players you genuinely cannot wait on. A high VAL in the
    THERE group means you can spend this pick elsewhere and come back for him.
    With no position, it also prints a scarcity line per position: how many
    survive to your next turn. That is the run-detection view. A position with
    almost nothing left in STILL THERE is one to take now even if a player at
    another position grades higher.

    Pass a position to see only that one. Defenders in an IDP league land in
    NO ADP, where timing is unknown. The draft slot comes from <SLUG>_DRAFT_POS
    in the environment; pass slot only to override it."""
    limit = max(1, min(limit, MAX_LIMIT))
    cfg = get_league(league)
    slot = slot or cfg.draft_slot
    if not slot:
        return (f"no draft slot for '{league}'. set {league.upper()}_DRAFT_POS in .env, "
                f"or pass one explicitly")
    c = client_for(league)
    teams, rounds = c.team_count(), c.roster_size()
    picks = snake_picks(slot, teams, rounds)
    nxt = next_pick(on_clock, picks)

    all_rows, _ = build_board(league, c.free_agents(position=None, limit=250),
                              c.roster_slots(), c.team_count())
    rows = filter_pos(all_rows, position)
    gone, contested, safe, unknown = partition(rows, nxt)

    mine = ", ".join(str(p) for p in picks[:6])
    head = (f"{cfg.name}{f' [{position}]' if position else ''}: "
            f"slot {slot} of {teams}, {rounds} rounds\n"
            f"your picks: {mine}...\n"
            f"on the clock: {on_clock}, your next pick: {nxt or 'none left'}\n")

    def scarcity() -> str:
        """How each position depletes before your next turn. Reads across the
        whole pool regardless of the position filter, since the point is
        comparing positions."""
        by_pos: dict[str, list[int]] = {}
        for r in all_rows:
            g, c_, s, u = partition([r], nxt)
            slot_i = 0 if g else 1 if c_ else 2 if s else 3
            counts = by_pos.setdefault(r.state.pos, [0, 0, 0, 0])
            counts[slot_i] += 1
        lines = ["\nSCARCITY: how many survive to your next pick",
                 f"  {'POS':<5} {'gone':>5} {'flip':>5} {'left':>5} {'noadp':>6}"]
        for pos, (g, c_, s, u) in sorted(by_pos.items(), key=lambda kv: -kv[1][2]):
            lines.append(f"  {pos:<5} {g:>5} {c_:>5} {s:>5} {u:>6}")
        return "\n".join(lines)

    def block(title, group, n):
        # Sort by VOR (board order), NOT by VAL. Every player in GONE is
        # someone you cannot wait on, so the cost of waiting is already zero
        # for all of them and the only question left is who is best. Sorting
        # these by VAL buries the top-ranked player under a mid-round bargain,
        # which is nonsense at the top of a draft. VAL chooses between
        # buckets; VORP orders within them.
        if not group:
            return f"\n{title}\n  (none)"
        best = sorted(group, key=lambda r: r.overall_rank)[:n]
        lines = [f"\n{title}",
                 f"  {'#':>4} {'POS':<5} {'PLAYER':<20} {'TM':<3} {'VOR':>6} "
                 f"{'ADP':>5} {'VAL':>4}  FLAG"]
        for r in best:
            lines.append(
                f"  {'#' + str(r.overall_rank):>4} "
                f"{r.state.pos + str(r.avg_pos_rank):<5} "
                f"{r.state.name[:20]:<20} {r.state.team or '--':<3} "
                f"{r.vorp:>6.1f} {r.adp:>5.1f} {r.value:>+4d}"
                f"{'  ' + r.flag if r.flag else ''}")
        return "\n".join(lines)

    return (head
            + block(f"GONE before pick {nxt} -- take one of these now", gone, limit)
            + block(f"COIN FLIP around pick {nxt}", contested, max(4, limit // 2))
            + block(f"STILL THERE at {nxt} -- you can wait", safe, max(4, limit // 2))
            + (f"\n\nNO ADP (timing unknown): {len(unknown)} players, mostly IDP"
               if unknown else "")
            + (scarcity() if not position else ""))


@mcp.tool
def get_my_roster(league: str) -> str:
    """My current roster in one league. Empty until the draft happens."""
    c = client_for(league)
    return roster_table(c.my_roster(), f"{get_league(league).name} roster")


@mcp.tool
def get_player_notes(league: str, name: str) -> str:
    """Everything known about one player: board numbers, late-breaking injury or
    legal news, and which analysts listed him. Use when a row shows NEWS!,
    OUT?, SPLIT, or before spending an early pick. News and opinion are both
    deliberately kept out of the projection blend."""
    c = client_for(league)
    rows, _ = build_board(league, c.free_agents(position=None, limit=250),
                          c.roster_slots(), c.team_count())
    want = name.strip().lower()
    hit = next((r for r in rows if want in r.state.name.lower()), None)
    if hit is None:
        return f"'{name}' is not in the available pool for {league} (already drafted, or not found)"

    out = [f"{hit.state.name} ({hit.state.pos} {hit.state.team})",
           f"  overall #{hit.overall_rank}  {hit.state.pos}{hit.avg_pos_rank}  "
           f"espn {hit.espn_pts:.1f}  "
           f"pff {hit.pff_pts if hit.pff_pts is None else round(hit.pff_pts, 1)}  "
           f"cons {hit.avg:.1f}",
           f"  adp {hit.adp}  val {hit.value}  {hit.flag or ''}".rstrip()]
    if hit.news is not None:
        out.append(f"  NEWS {hit.news.describe()}")
    if hit.buzz is None:
        out.append("  analysts: not mentioned")
    else:
        out.append(f"  analysts ({hit.buzz.up} positive, {hit.buzz.down} negative"
                   f"{', THEY DISAGREE' if hit.buzz.split else ''}):")
        out.append(f"    {hit.buzz.describe()}")
    return "\n".join(out)


@mcp.tool
def health_check() -> str:
    """Per-league connection status. Use when a tool errors, or to check
    whether the ESPN cookies have expired."""
    lines = []
    for slug, cfg in leagues().items():
        try:
            lines.append(f"{slug} ({cfg.platform}): OK  {client_for(slug).ping()}")
        except Exception as exc:
            lines.append(f"{slug} ({cfg.platform}): FAIL  {type(exc).__name__}: {exc}")
    return "\n".join(lines) or "no leagues configured"


def _auth_middleware(app):
    """Verify the bearer token in-process, independent of Cloudflare.
    A tunnel misconfiguration should not be a breach."""
    expected = f"Bearer {BEARER_TOKEN}" if BEARER_TOKEN else None

    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and expected:
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"").decode()
            if not hmac.compare_digest(got, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return wrapped


def main() -> None:
    if not BEARER_TOKEN:
        raise SystemExit("COMBINE_BEARER_TOKEN is unset. refusing to serve unauthenticated.")
    import uvicorn

    uvicorn.run(_auth_middleware(mcp.http_app(path=PATH)), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
