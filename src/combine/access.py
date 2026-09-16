"""Proof that Cloudflare Access let this request through.

The app has no login of its own and it should not get one. Every page load acts
as the ESPN session in `.env`, so the thing guarding it has to refuse
*unauthenticated requests before they reach Python*, which is what Access does
and what a login page inside Streamlit cannot: by the time Streamlit is deciding
whether to draw a login form, the request has already arrived at a process
holding live ESPN cookies, a Discord token and a PFF key.

So authentication lives at the edge. Google is the identity provider, the allow
policy names the exact addresses, and Cloudflare turns away everybody else at
its own network before a packet reaches the mini.

What this module adds is the second lock. Access puts a signed JWT on every
request it forwards, in `Cf-Access-Jwt-Assertion`, and verifying it here means
the app is safe even when the first lock is not there: bound to `0.0.0.0` by
mistake, reached from the LAN on port 8501, a tunnel ingress rule pointing at
the wrong hostname, an Access policy somebody widened. None of those are
hypothetical the way a signature forgery is.

It is off until configured, so a checkout on a laptop still runs. When it IS
configured it fails closed: no token, a bad signature, the wrong audience or an
address off the list all stop the page rather than logging and continuing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

HEADER = "cf-access-jwt-assertion"


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def team() -> str:
    """`yourteam.cloudflareaccess.com`, from Zero Trust → Settings → Custom
    Pages, or the domain in the login URL."""
    return _env("COMBINE_ACCESS_TEAM").removeprefix("https://").rstrip("/")


def audience() -> str:
    """The Application Audience (AUD) tag of the Access application. It is the
    only thing tying a token to THIS app rather than to any other application
    on the same Cloudflare team."""
    return _env("COMBINE_ACCESS_AUD")


def allowed() -> frozenset[str]:
    """Addresses this app will serve, lowercased. Optional: the Access policy
    is already an allowlist, and this is the same list written down a second
    time in a place Cloudflare cannot edit."""
    raw = _env("COMBINE_ACCESS_EMAILS")
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def configured() -> bool:
    return bool(team() and audience())


@dataclass(frozen=True)
class Verdict:
    ok: bool
    email: str = ""
    why: str = ""
    enforced: bool = True

    @property
    def off(self) -> bool:
        return not self.enforced


@lru_cache(maxsize=4)
def _keys(url: str):
    from jwt import PyJWKClient

    return PyJWKClient(url)


def _decode(token: str, team_domain: str, aud: str) -> dict:
    import jwt

    url = f"https://{team_domain}/cdn-cgi/access/certs"
    key = _keys(url).get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=["RS256"], audience=aud)


def token_from(headers) -> str:
    """The assertion, however the header arrives.

    Header names are case-insensitive on the wire and Streamlit hands them back
    in whatever case the proxy used, so this does not trust the spelling.
    """
    if not headers:
        return ""
    try:
        items = headers.items()
    except AttributeError:
        return ""
    for name, value in items:
        if str(name).lower() == HEADER:
            return str(value or "")
    return ""


def check(headers, decode=None) -> Verdict:
    """Whether to serve this request. Fails closed once configured."""
    if not configured():
        return Verdict(ok=True, why="Cloudflare Access is not configured",
                       enforced=False)

    token = token_from(headers)
    if not token:
        return Verdict(ok=False, why="this request did not come through "
                                     "Cloudflare Access")
    try:
        claims = (decode or _decode)(token, team(), audience())
    except Exception as exc:
        return Verdict(ok=False, why=f"the Access token did not verify: "
                                     f"{type(exc).__name__}")

    email = str(claims.get("email") or "").lower()
    listed = allowed()
    if listed and email not in listed:
        return Verdict(ok=False, email=email,
                       why=f"{email or 'that account'} is not on this app's "
                           f"list")
    return Verdict(ok=True, email=email)
