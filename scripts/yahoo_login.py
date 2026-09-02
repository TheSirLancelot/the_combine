#!/usr/bin/env python3
"""One-time Yahoo OAuth. Run on the mini with a browser available.

Prereq: register an app at https://sports.yahoo.com/developer/ with the
Fantasy Sports read scope and redirect URI https://localhost:8000, then put
YAHOO_CONSUMER_KEY / YAHOO_CONSUMER_SECRET in .env.

yahoofantasy persists the refresh token itself. That token is long lived, so
unlike ESPN this is roughly an annual chore.
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from combine.config import env  # noqa: E402

key, secret = env("YAHOO_CONSUMER_KEY"), env("YAHOO_CONSUMER_SECRET")
if not key or not secret:
    sys.exit("set YAHOO_CONSUMER_KEY and YAHOO_CONSUMER_SECRET in .env first")

os.environ.setdefault("YAHOO_CONSUMER_KEY", key)
os.environ.setdefault("YAHOO_CONSUMER_SECRET", secret)
raise SystemExit(subprocess.call(["yahoofantasy", "login"]))
