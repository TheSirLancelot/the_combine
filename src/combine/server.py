"""FastMCP server. Streamable HTTP, bearer-authenticated, bound to loopback.

Phase 0 ships health_check only. Phase 1.3 adds the league tools.
"""

from __future__ import annotations

import hmac

from fastmcp import FastMCP

from .config import BEARER_TOKEN, HOST, PATH, PORT, leagues
from .platforms import client_for

mcp = FastMCP("combine")


@mcp.tool
def health_check() -> str:
    """Per-league connection status. Use this when a league tool errors, or to
    check whether the ESPN cookies have expired."""
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
