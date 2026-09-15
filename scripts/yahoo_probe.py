#!/usr/bin/env python3
"""Work out WHY Yahoo is refusing, without guessing. Run from the repo root.

    uv run python scripts/yahoo_probe.py

A 401 on a token that was issued seconds ago has a small number of causes and
they need different fixes, so this asks a series of endpoints and prints what
each one says. The pattern of answers is the diagnosis:

  * Everything 401s  -> the token carries no fantasy scope. Yahoo issues a
    profile-only token when the authorize request asks for no scope, and then
    refuses every fantasy call. The fix is `scope=fspt-r` on the authorize
    request, which scripts/yahoo_login.py now sends -- and it only takes effect
    on a token issued AFTER that change, so the login has to be re-run. This is
    not an app permission: the app page offers no Fantasy Sports checkbox to
    tick.
  * 2025 works, 2026 401s -> the season is not open to the API yet, which is a
    waiting problem rather than a configuration one.
  * Everything works -> the failure was in yahoofantasy's own handling and the
    adapter should talk to the API directly.

Prints status codes and Yahoo's own error text. No token is printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests

BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

# Narrowest to broadest. The first one that works tells you where the line is.
PROBES = [
    ("who am I", "users;use_login=1"),
    ("my games", "users;use_login=1/games"),
    ("nfl game meta", "game/nfl"),
    ("nfl 2025 game id", "games;game_codes=nfl;seasons=2025"),
    ("nfl 2026 game id", "games;game_codes=nfl;seasons=2026"),
    ("my 2025 leagues", "users;use_login=1/games;game_keys=461/leagues"),
]


def saved_at() -> str:
    """When the stored token was written.

    Here because of a real half hour lost to it: the probe reads whatever is
    saved, so running it after a fix but before re-running the login tests the
    OLD token and reproduces the old failure exactly. The age makes that
    obvious instead of confusing.
    """
    from datetime import datetime

    from yahoofantasy.util.persistence import load

    when = load("auth__time", default=None, ttl=-1)
    if not when:
        return "unknown"
    stamp = datetime.fromtimestamp(when)
    mins = (datetime.now() - stamp).total_seconds() / 60
    return f"{stamp:%Y-%m-%d %H:%M} ({mins:.0f} min ago)"


def token() -> str:
    """The access token the library holds, refreshing it if stale."""
    from yahoofantasy import Context

    ctx = Context()
    # Private, but this is a diagnostic: going through the public API is what
    # fails, so the point is to hold the token and ask Yahoo directly.
    if not ctx._access_token:
        ctx._get_access_token()
    return ctx._access_token


def main() -> int:
    try:
        access = token()
    except Exception as exc:
        print(f"could not load a token: {type(exc).__name__}: {exc}")
        print("run scripts/yahoo_login.py first")
        return 2

    print(f"token loaded, {len(access)} chars, saved {saved_at()}")
    print("if that is not from the last few minutes, this is testing an OLD "
          "token\n")
    headers = {"Authorization": f"Bearer {access}", "User-Agent": "Mozilla/5.0"}
    results = {}
    for label, path in PROBES:
        try:
            resp = requests.get(f"{BASE}/{path}", headers=headers, timeout=30)
        except Exception as exc:
            print(f"{label:<20} FAILED  {type(exc).__name__}: {exc}")
            continue
        results[label] = resp.status_code
        print(f"{label:<20} {resp.status_code}  {path}")
        if not resp.ok:
            body = " ".join(resp.text.split())[:300]
            print(f"{'':<20} {body}")
        print()

    ok = {k for k, v in results.items() if v == 200}
    print("-" * 60)
    if not ok:
        print("Nothing authorised: this token carries no fantasy scope.")
        print()
        print("Check the 'saved' time above first. If the token predates the")
        print("last change to scripts/yahoo_login.py, re-run that login and")
        print("probe again -- this reads whatever is stored, so it will happily")
        print("retest a stale token and reproduce the old failure.")
        print()
        print("If the token IS fresh, then the consent screen is the thing to")
        print("watch: it has to mention Fantasy Sports. If it only asks about")
        print("your profile, Yahoo is not honouring scope=fspt-r for this app,")
        print("and no amount of re-authorising will change that.")
        print()
        print("This is not an app permission. The app page offers only OpenID")
        print("Connect and TW Auction, on an existing app and a new one, so")
        print("there is nothing to tick there.")
    elif "nfl 2026 game id" not in ok and "nfl 2025 game id" in ok:
        print("2025 answers and 2026 does not, so the credentials are fine and")
        print("the 2026 season is not exposed to the API yet. That is a waiting")
        print("problem. The hand-entered league keeps working meanwhile.")
    else:
        print("The API answers. The failure was inside yahoofantasy rather than")
        print("at Yahoo, so the adapter should make these calls directly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
