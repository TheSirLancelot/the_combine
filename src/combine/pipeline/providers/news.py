"""Late-breaking player status: injuries, suspensions, legal.

The projection exports are snapshots. This is the layer that says the snapshot
is stale, which before a draft is often the highest-value information in the
system. Josh Jacobs is the worked example: ESPN still projects him fully, PFF
zeroed him, and neither file explains why.

Hand-curated from reporting, with a source and a date on every row so you can
tell how old a claim is. It is not a feed and it does not refresh itself.
Re-check the high severity rows the morning of a draft; hamstrings move fast.

severity drives how loudly the board shouts:
  high    changes a pick. out, IR, week-to-week, suspension risk
  medium  worth knowing. coming off a serious injury, role uncertainty
  low     context only. resolved injury, cleared for week 1
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

from ...config import DATA_DIR

NEWS_DIR = DATA_DIR / "news"


@dataclass(frozen=True)
class NewsItem:
    name: str
    severity: str
    status: str
    note: str
    source: str
    as_of: str
    team: str = ""
    pos: str = ""

    def describe(self) -> str:
        return f"[{self.severity}] {self.note} ({self.source}, {self.as_of})"


def available() -> bool:
    return NEWS_DIR.exists() and any(NEWS_DIR.glob("*.csv"))


def load() -> dict[str, NewsItem]:
    if not NEWS_DIR.exists():
        return {}
    out: dict[str, NewsItem] = {}
    for path in sorted(NEWS_DIR.glob("*.csv")):
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                item = NewsItem(
                    name=r["player"].strip(),
                    severity=r["severity"].strip().lower(),
                    status=r["status"].strip(),
                    note=r["note"].strip(),
                    source=r["source"].strip(),
                    as_of=r["as_of"].strip(),
                )
                out[item.name] = item
    return out
