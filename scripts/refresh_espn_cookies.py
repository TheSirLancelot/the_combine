#!/usr/bin/env python3
"""Rewrite ESPN_S2 / ESPN_SWID in .env in place.

ESPN's cookies die mid-season without warning. Recovery should be a 30 second
paste, not a debugging session.

  DevTools > Application > Cookies > fantasy.espn.com
  Copy espn_s2 and SWID (keep the braces on SWID).

  python scripts/refresh_espn_cookies.py
"""

import re
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parents[1] / ".env"


def main() -> int:
    if not ENV.exists():
        print(f"no {ENV}. copy .env.example first.", file=sys.stderr)
        return 1

    s2 = input("espn_s2: ").strip()
    swid = input("SWID:    ").strip()
    if not s2 or not swid:
        print("both values required", file=sys.stderr)
        return 1
    if not (swid.startswith("{") and swid.endswith("}")):
        print("SWID should keep its braces, e.g. {ABC-123}", file=sys.stderr)
        return 1

    text = ENV.read_text()
    for key, val in (("ESPN_S2", s2), ("ESPN_SWID", swid)):
        pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
        text = pattern.sub(f"{key}={val}", text) if pattern.search(text) else text + f"\n{key}={val}\n"
    ENV.write_text(text)

    print("written. restart the server:  launchctl kickstart -k gui/$UID/com.combine.server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
