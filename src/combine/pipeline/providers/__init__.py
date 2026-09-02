"""Projection providers.

A provider's only job is to turn one source's raw output into RawProjection.
It does not resolve canonical player ids (crosswalk.py does), does not apply
scoring (scoring.py does), and does not know what a league is.
Adding a source = one file here, aliases in the crosswalk, a weight in config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol


@dataclass(frozen=True)
class RawProjection:
    source_id: str
    source_name: str
    pos: str
    team: str | None
    stats: dict[str, float] = field(default_factory=dict)  # 'pass_yd', 'rec', 'rush_td', ...


class Provider(Protocol):
    name: str

    def fetch(self, season: int, week: int | None) -> Iterable[RawProjection]: ...
