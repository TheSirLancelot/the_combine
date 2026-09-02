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
def get_draft_pool(league: str, position: str = "", limit: int = 15) -> str:
    """Best available undrafted players for one league, ranked by that league's
    own scoring and grouped into tiers. This is the draft-day tool: during a
    draft it reflects who is still on the board. Optional position filter
    (QB, RB, WR, TE, K, D/ST, LB, DL, DB)."""
    limit = max(1, min(limit, MAX_LIMIT))
    c = client_for(league)
    states = c.free_agents(position=position or None, limit=limit)
    label = f"{get_league(league).name} available{f' at {position}' if position else ''}"
    return ranked_table(states, label)


@mcp.tool
def get_draft_board(league: str, position: str = "", limit: int = 20) -> str:
    """Best available players for one league with BOTH projection sources side
    by side: ESPN's and PFF's, each already scored under this league's rules,
    plus consensus, tiers, ADP and VAL. Use this over get_draft_pool when
    drafting. VAL is ADP minus our overall rank: positive means the room is
    letting him fall past where the numbers put him. FLAG calls out OUT?
    (ranked and drafted early but projected zero, so something happened),
    no-pff, VALUE/REACH, and source disagreement. Optional position filter."""
    limit = max(1, min(limit, MAX_LIMIT))
    c = client_for(league)
    # Unfiltered pool: overall_rank and the ADP comparison are only meaningful
    # against the whole board, so filter for display, not at the source.
    pool = c.free_agents(position=None, limit=250)
    rows, counts = build_board(league, pool)
    shown = filter_pos(rows, position)[:limit]
    label = f"{get_league(league).name} board{f' at {position}' if position else ''}"
    unmatched = counts.get("unmatched", 0) + counts.get("ambiguous", 0)
    note = f"\n({unmatched} of {len(pool)} had no PFF match)" if unmatched else ""
    return render_board(shown, label) + note


@mcp.tool
def get_my_roster(league: str) -> str:
    """My current roster in one league. Empty until the draft happens."""
    c = client_for(league)
    return roster_table(c.my_roster(), f"{get_league(league).name} roster")


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
