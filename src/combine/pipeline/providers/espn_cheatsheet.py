"""ESPN's 2026 Ultimate Cheat Sheet: nine analysts' opinion lists.

Explicitly NOT part of the projection blend. Opinion has no scale and no
scoring format, so folding it into CONS would corrupt a number that currently
means something. It rides alongside as sentiment.

The useful signal is not the tally, it is the contradictions. Kenneth Walker
III is on Karabell's do-not-draft AND Schefter's targets AND Clay's more-TDs
AND Field's favorites. Tucker Kraft is a do-draft and a fewer-TDs. When ESPN's
own people disagree that hard, the projections are not going to settle it and
you are on your own read.

Hand-transcribed from the PDF (data/espn/cheatsheet_2026.csv). The PDF is a
four-column magazine layout that does not parse reliably, so this is typed,
not extracted. Two entries were dropped as not being players: "Jaguars
receivers" and "Rookie receivers".
"""

from __future__ import annotations

import csv
from collections import defaultdict

from ...config import DATA_DIR

SHEET = DATA_DIR / "espn" / "cheatsheet_2026.csv"

# Human-readable, and short enough to print on one line.
LABELS = {
    "karabell_do_not_draft": "Karabell DO NOT DRAFT",
    "karabell_do_draft": "Karabell do draft",
    "schefter_target": "Schefter target",
    "clay_more_tds": "Clay: more TDs",
    "clay_fewer_tds": "Clay: fewer TDs",
    "loza_late_flier": "Loza late flier",
    "moody_insurance_rb": "Moody insurance RB",
    "moody_value": "Moody value",
    "field_favorite": "Field favorite",
}


class Sentiment:
    def __init__(self, lists: list[str], polarity: list[int]):
        self.lists = lists
        self.up = sum(1 for p in polarity if p > 0)
        self.down = sum(1 for p in polarity if p < 0)

    @property
    def net(self) -> int:
        return self.up - self.down

    @property
    def split(self) -> bool:
        """Analysts contradicting each other on the same player."""
        return self.up > 0 and self.down > 0

    def describe(self) -> str:
        return ", ".join(LABELS.get(x, x) for x in self.lists)


def available() -> bool:
    return SHEET.exists()


def load() -> dict[str, Sentiment]:
    """name -> Sentiment. Keyed on the verbatim name; the caller crosswalks."""
    if not SHEET.exists():
        return {}
    lists: dict[str, list[str]] = defaultdict(list)
    pol: dict[str, list[int]] = defaultdict(list)
    with SHEET.open(newline="") as fh:
        for r in csv.DictReader(fh):
            name = r["player"].strip()
            lists[name].append(r["list"].strip())
            pol[name].append(int(r["polarity"]))
    return {n: Sentiment(lists[n], pol[n]) for n in lists}
