"""Apply a league's scoring rules to a projected or actual stat line.

Owed since the draft, and finally on the critical path: the Yahoo league has no
API yet, so its projections have to be built by taking ESPN's raw stat-line
projections, which are league independent, and scoring them under Yahoo's rules.

The vocabulary is ESPN's numeric stat IDs, not its stat names, and that
distinction cost a debugging round worth recording. A scoring table written in
names looks fine and validates at 92% on one league and 0% on the other,
because espn-api's id-to-name map does not cover every stat a league can score.
RCL pays a point per 25 passing yards (id 8) and per 10 rushing yards (id 28),
DMWD pays 5 for a 50+ yard field goal (id 198), and none of those ids have
names, so a name-keyed table drops them silently and every quarterback comes out
11 points light. The stat lines carry those as bare numeric keys precisely
because there is no name for them.

So tables are keyed by id internally. A hand-written table may use names, which
are resolved to ids on load, since a config file full of integers is not
reviewable. Values are POINTS PER UNIT, so passing yards at one per twenty five
is 0.04, and scoring is a dot product.

That flatness is what makes the engine checkable: ESPN's own rules are already
id-keyed points per unit, so we can score ESPN's stat lines under ESPN's rules
and compare against the points ESPN itself published. See `validate`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import CONFIG_DIR


def _name_to_ids() -> dict[str, list[int]]:
    """name -> every stat id espn-api gives that name.

    Six names are ambiguous: passingYards is both 3 and 22, receivingReceptions
    both 41 and 53, and so on. Which one a league scores is a property of the
    league, so a single reverse map cannot be right for everyone. Collapsing
    them silently is how every quarterback in DMWD lost fifteen points.
    """
    from espn_api.football.constant import PLAYER_STATS_MAP

    out: dict[str, list[int]] = {}
    for stat_id, name in PLAYER_STATS_MAP.items():
        out.setdefault(name, []).append(stat_id)
    return {name: sorted(ids) for name, ids in out.items()}


def resolve(key, prefer: frozenset[int] = frozenset()) -> int | None:
    """One stat key to one stat id.

    `prefer` is the set of ids the scoring table actually uses, so an ambiguous
    name resolves to the id this league scores. With nothing to prefer, the
    lowest id wins, which is stable so both sides of a comparison agree.
    """
    if str(key).lstrip("-").isdigit():
        return int(key)
    candidates = _name_to_ids().get(key)
    if not candidates:
        return None
    for stat_id in candidates:
        if stat_id in prefer:
            return stat_id
    return candidates[0]


def normalize(breakdown: dict, prefer: frozenset[int] = frozenset()) -> dict[int, float]:
    """A stat line keyed by ESPN stat id.

    Lines come back with a mix: named keys for stats espn-api knows, bare
    numeric keys for the ones it does not. Both are ids underneath, and the
    unnamed ones are exactly the league-specific scoring buckets that matter.
    """
    out: dict[int, float] = {}
    for key, value in breakdown.items():
        if not isinstance(value, (int, float)):
            continue
        stat_id = resolve(key, prefer)
        if stat_id is not None:
            out[stat_id] = float(value)
    return out


# Defensive and team-defense stat ids, read off the live scoring formats of both
# ESPN leagues rather than from documentation, per the standing rule in this
# repo. Both leagues use the SAME ids for these; what differs is who may earn
# them. RCL has IDP slots so an individual defender scores them, DMWD and the
# Yahoo league have only a team D/ST slot, so ESPN pays these to the team
# defense and to nobody else.
#
# This matters because ESPN projects defensive stats for two-way players. Travis
# Hunter is the worked example: scoring his projected interceptions in a league
# with no IDP slots inflated him by 0.16 points, which is small but is the
# engine being wrong about who earns what, and in the Yahoo league it would be
# wrong the same way.
DEFENSE_IDS = frozenset({
    89, 90, 91, 92,                      # points allowed bands
    95, 96, 97, 98, 99,                  # int, fumble recovery, blocked kick, safety, sack
    103, 104,                            # interception and fumble return TDs
    106, 107, 108, 112, 113,             # forced fumble, tackles, stuffs, passes defensed
    120, 123, 124, 125,                  # more points allowed bands
    128, 129, 130, 132, 133, 134, 135, 136, 187,   # yards allowed bands
})
# Deliberately NOT in that set: kickoff and punt return touchdowns (101, 102)
# and blocked-kick return touchdowns (93). Those live in the same id band and
# read like defensive stats, but a returner earns them, and returners are
# receivers and backs. Excluding them made every return-man project low, Parker
# Washington by 0.21, which is how the boundary of this set was actually found.
DEFENSE_SLOTS = frozenset({"D/ST", "DST", "DEF", "DP"})
# Slots that mean a league pays individual defenders.
IDP_SLOTS = frozenset({"LB", "DL", "DB", "DP", "DE", "DT", "CB", "S", "EDGE"})


@dataclass(frozen=True)
class ScoringTable:
    """Points per unit, keyed by ESPN stat id.

    `idp` says whether individual defenders score defensive stats in this
    league. It changes who is paid for a defensive stat line, not what it is
    worth.
    """
    name: str
    rules: dict[int, float]
    idp: bool = False

    def score(self, breakdown: dict, position: str | None = None) -> float:
        """Points for one stat line.

        Unpriced stats are ignored rather than raising: ESPN emits plenty no
        league scores (targets, attempts, completion percentage) and every
        league ignores most of them. A missing stat is a zero, not an error.
        """
        prefer = frozenset(self.rules)
        skip_defense = (
            not self.idp
            and position is not None
            and position.upper() not in DEFENSE_SLOTS
        )
        return sum(value * self.rules[stat_id]
                   for stat_id, value in normalize(breakdown, prefer).items()
                   if stat_id in self.rules
                   and not (skip_defense and stat_id in DEFENSE_IDS))

    def unscored(self, breakdown: dict) -> list[int]:
        """Stat ids present in the line that this table does not price. For
        eyeballing a new league's table, not for runtime."""
        return sorted(i for i, v in normalize(breakdown, frozenset(self.rules)).items()
                      if i not in self.rules and v)


class UnknownStat(KeyError):
    pass


def load_table(path: Path) -> ScoringTable:
    """Read a hand-entered league file, as used for Yahoo while its API is in
    review. Names in the file are resolved to ids here.

    An unresolvable name is fatal rather than skipped. A silently dropped
    scoring rule is the exact bug this module was written to catch, and a table
    that quietly prices nine of your ten categories is worse than one that
    refuses to load.
    """
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    rules: dict[int, float] = {}
    unknown = []
    for key, points in raw["scoring"].items():
        stat_id = resolve(key)
        if stat_id is None:
            unknown.append(key)
            continue
        rules[stat_id] = float(points)
    if unknown:
        raise UnknownStat(f"{path.name}: no ESPN stat id for {', '.join(unknown)}")
    slots = raw.get("slots", {})
    return ScoringTable(name=raw.get("name", path.stem), rules=rules,
                        idp=any(s in IDP_SLOTS for s in slots))


def load_league_file(slug: str) -> dict:
    with (CONFIG_DIR / f"{slug}_league.toml").open("rb") as fh:
        return tomllib.load(fh)


def espn_table(client) -> ScoringTable:
    """A league's own ESPN scoring, which is already id-keyed points per unit.

    No name lookup anywhere in here, which is the point: this table is complete
    by construction, including the league-specific buckets that have no names.
    """
    return ScoringTable(
        name=f"{client.slug} (espn)",
        rules={i: float(p) for i, p in client.scoring_rules().items() if p},
        idp=any(s in IDP_SLOTS for s in client.roster_slots()),
    )


def validate(client, week: int, tolerance: float = 0.05) -> dict:
    """Score ESPN's stat lines under ESPN's own rules and compare to the points
    ESPN published for the same player.

    This is the only honest way to know the engine is right before pointing it
    at a league whose real numbers we cannot see. A mismatch here is a bug in
    the engine or a hole in the id map; a match means the Yahoo table is only as
    wrong as the Yahoo table itself.
    """
    table = espn_table(client)
    rows, worst = [], []
    for box in client.league.box_scores(week):
        for player in (box.home_lineup or []) + (box.away_lineup or []):
            stats = getattr(player, "stats", {}).get(week, {})
            breakdown = stats.get("projected_breakdown") or {}
            published = stats.get("projected_points")
            if not breakdown or published is None:
                continue
            ours = table.score(breakdown, position=player.position)
            rows.append((player.name, player.position, published, ours))
            if abs(ours - published) > tolerance:
                worst.append((player.name, player.position, published, ours))
    worst.sort(key=lambda r: -abs(r[3] - r[2]))
    return {
        "n": len(rows),
        "matched": len(rows) - len(worst),
        "mismatched": len(worst),
        "worst": worst[:10],
        "max_error": max((abs(r[3] - r[2]) for r in rows), default=0.0),
    }
