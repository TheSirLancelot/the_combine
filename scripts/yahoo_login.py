#!/usr/bin/env python3
"""One-time Yahoo OAuth. Run on the mini, from the repo root.

    uv run python scripts/yahoo_login.py

Prereq: an app at https://developer.yahoo.com/apps/ with Fantasy Sports READ
permission and redirect URI exactly https://localhost:8000, then
YAHOO_CONSUMER_KEY and YAHOO_CONSUMER_SECRET in .env.

This deliberately does NOT call `yahoofantasy login`, which is what it used to
do. That command stands up an HTTPS server to catch the redirect and builds it
with `ssl.wrap_socket`, removed from Python in 3.12; on this 3.14 venv it dies
before you can authorise anything. It also reads YAHOO_CLIENT_ID and
YAHOO_CLIENT_SECRET, not the names in our .env, so it would have prompted for
both anyway.

No server is needed. The redirect lands on https://localhost:8000/?code=...,
which simply fails to load, and the code is sitting in the address bar. Copying
it is less work than making a self-signed certificate behave, and it works on
any Python.

The refresh token is long lived, so this is roughly an annual chore rather than
the recurring one ESPN cookies are.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from time import time
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

from combine.config import env

OAUTH = "https://api.login.yahoo.com/oauth2"
REDIRECT = "https://localhost:8000"

# Yahoo's Fantasy Sports read scope. This has to be asked for in the AUTHORIZE
# request; it is not a checkbox on the app.
#
# Worth spelling out, because the first attempt sent no scope at all and the
# symptom was misleading. OAuth completed, a token saved, and then every single
# fantasy endpoint answered 401 with
# oauth_problem="additional_authorization_required" -- which reads like a
# missing app permission, and the app's API Permissions list offers only
# OpenID Connect and TW Auction. There is nothing to tick there. Without a
# scope parameter Yahoo issues a profile-only token, and that is the token that
# gets refused.
#
# `fspt-w` is read AND write. This tool is read-only against Yahoo, so it asks
# for `fspt-r` and should keep asking for exactly that: a token that cannot
# write is a guarantee no future bug can make a roster move, which is worth
# more than the convenience.
# Overridable because the right value is currently an open question: Yahoo
# answered `error=invalid_scope` to fspt-r on this app (2026-09-15). Set
# YAHOO_SCOPE in .env to try another, or to an empty string to send none.
SCOPE = "fspt-r"


def error_from(pasted: str) -> str:
    """Yahoo's own complaint, when the redirect carries one instead of a code.

    Worth reading rather than being sent on as if it were a code: an
    `invalid_scope` redirect means the authorize request was refused before any
    login happened, which is a different problem from a code that fails to
    exchange.
    """
    query = parse_qs(urlparse(pasted).query)
    if "error" not in query:
        return ""
    error = query["error"][0]
    detail = query.get("error_description", [""])[0]
    return f"{error}: {detail}" if detail else error


def code_from(pasted: str) -> str:
    """Accept the whole redirected URL or just the code.

    Pasting the address bar is the obvious thing to do, so it should work.
    """
    pasted = pasted.strip()
    if "code=" in pasted:
        found = parse_qs(urlparse(pasted).query).get("code")
        if found:
            return found[0]
    return pasted


def exchange(key: str, secret: str, code: str) -> dict:
    """Trade the code for tokens.

    Two redirect_uri values are tried because Yahoo is inconsistent about which
    it wants here: the spec says it must match the authorize call, while
    yahoofantasy's own login sends the literal "oob" and evidently works. Rather
    than pick one and hope, try the spec-compliant value first and fall back,
    then say which one answered so the next person does not have to rediscover
    it.
    """
    attempts = [("the registered redirect", REDIRECT), ("oob", "oob")]
    problems = []
    for label, redirect in attempts:
        resp = requests.post(
            f"{OAUTH}/get_token",
            data={"client_id": key, "client_secret": secret,
                  "grant_type": "authorization_code", "code": code,
                  "redirect_uri": redirect},
            timeout=30)
        if resp.ok:
            print(f"  token exchange accepted {label}")
            return resp.json()
        problems.append(f"{label}: {resp.status_code} {resp.text[:200]}")
    raise SystemExit("Yahoo refused the code.\n  " + "\n  ".join(problems)
                     + "\n\nA code is single use and expires quickly, so if you "
                       "have just retried, start again from the top.")


def main() -> int:
    scope = env("YAHOO_SCOPE")
    scope = SCOPE if scope is None else scope
    key, secret = env("YAHOO_CONSUMER_KEY"), env("YAHOO_CONSUMER_SECRET")
    if not key or not secret:
        raise SystemExit("set YAHOO_CONSUMER_KEY and YAHOO_CONSUMER_SECRET in "
                         ".env first (they are on your app's page at "
                         "https://developer.yahoo.com/apps/)")

    url = f"{OAUTH}/request_auth?" + urlencode(
        {"client_id": key, "redirect_uri": REDIRECT, "response_type": "code",
         **({"scope": scope} if scope else {})})
    print("Opening Yahoo for authorisation. If the browser does not open, use:")
    print(f"\n  {url}\n")
    webbrowser.open_new_tab(url)

    print(f"Asking for scope {scope!r}." if scope
          else "Sending no scope (Yahoo will issue a profile-only token).")
    print("The consent screen should mention Fantasy Sports. If it only asks")
    print("about your profile, stop: the token will not work and pasting the")
    print("code just repeats the last failure.\n")
    print("Approve the app. The browser will then fail to load a page at")
    print(f"{REDIRECT} -- that is expected, there is nothing listening there.")
    print("Copy the whole address from the bar and paste it here.\n")
    pasted = input("Redirected URL (or just the code): ")
    complaint = error_from(pasted)
    if complaint:
        raise SystemExit(
            f"\nYahoo refused the authorize request: {complaint}\n\n"
            f"Nothing was wrong with the paste; this came back instead of a "
            f"code.\nIf it says invalid_scope, this app is not entitled to "
            f"{SCOPE!r}.\nThat is a Yahoo-side entitlement, not something this "
            f"script can\nwork around. See the Yahoo section of BUILD_GUIDE.md.")
    code = code_from(pasted)
    if not code:
        raise SystemExit("nothing pasted")

    body = exchange(key, secret, code)
    if not body.get("refresh_token"):
        raise SystemExit(f"no refresh token in the response: {sorted(body)}")

    # The library's own save(), so the file format cannot drift from what
    # Context() expects to read back. It writes `.yahoofantasy` relative to the
    # CWD, which is why this says to run from the repo root.
    from yahoofantasy.util.persistence import get_persistence_filename, save

    save("auth", {
        "client_id": key,
        "client_secret": secret,
        "access_token": body["access_token"],
        "access_token_expires": time() + body.get("expires_in", 3600),
        "refresh_token": body["refresh_token"],
    })
    where = Path(get_persistence_filename("")).resolve()
    print(f"\nSaved to {where}")
    print("That file holds your secret and refresh token. It is gitignored;")
    print("keep it that way and do not copy it anywhere.")

    # Prove it end to end rather than declaring success on a 200. This also
    # prints the league ids that still have to go in .env.
    print("\nChecking the token works...")
    from yahoofantasy import Context

    from combine.config import SEASON
    from combine.platforms.yahoo import ensure_game

    ctx = Context()
    try:
        # The library's season -> game id table is hardcoded and ends at 2025,
        # so this has to be resolved from Yahoo before any call for 2026.
        print(f"  {SEASON} NFL game id: {ensure_game(ctx, SEASON)}")
        leagues = ctx.get_leagues("nfl", SEASON)
    except Exception as exc:
        print(f"  authorised, but the first call failed: {type(exc).__name__}: {exc}")
        print("  the token is saved; this is worth probing before writing the adapter.")
        return 1
    print("  working. Your NFL leagues this season:\n")
    for league in leagues:
        print(f"    {league.name}")
        print(f"      YAHOO_LEAGUE_ID={league.id}")
    print("\nPut the right league id in .env, then we can probe the API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
