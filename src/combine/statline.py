"""What a player actually did, in the words a box score uses.

ESPN hands back a raw breakdown per player per week: `passingCompletions: 15`,
`defensiveSoloTackles: 5`, and so on, mixed in with numeric stat ids we have no
vocabulary for and derived figures nobody reads (`passingCompletionPercentage`).
This turns the part that is readable into one line.

Three rules, all of them about not inventing anything:

  * Only keys ESPN sent, and only the ones named. A numeric id we cannot name is
    dropped rather than guessed at, because a stat line with a wrong label on it
    is worse than a short one.
  * Nothing derived. Every number printed here is a number ESPN printed; no
    yards per carry we worked out ourselves, no totals we added up.
  * Zeros are absent, not zero. '0 TD' on every line makes the line that says
    '2 TD' harder to spot, which is the whole job.

The phases are ordered the way a box score orders them, so a quarterback who
also ran reads passing first, and the line for a player who did one thing is
short enough to sit under his name on a phone.
"""

from __future__ import annotations

from collections.abc import Mapping

Stats = Mapping[str, float]


def _n(v: float) -> str:
    """'155', not '155.0'. Counting stats arrive as floats and none of them are
    fractional; the one that could be is a percentage, which we do not print."""
    return str(int(v)) if float(v) == int(v) else f"{v:.1f}"


def _get(s: Stats, key: str) -> float:
    try:
        return float(s.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bit(s: Stats, key: str, unit: str) -> str:
    v = _get(s, key)
    return f"{_n(v)} {unit}" if v else ""


def _ratio(s: Stats, made: str, missed: str, unit: str) -> str:
    """'2/3 FG'. The denominator is made plus missed because ESPN sends no
    attempts for kicks, and an attempt count we assembled is still an attempt
    count ESPN sent — the two halves are both its numbers."""
    m, x = _get(s, made), _get(s, missed)
    return f"{_n(m)}/{_n(m + x)} {unit}" if m or x else ""


def _join(parts) -> str:
    return ", ".join(p for p in parts if p)


def _passing(s: Stats) -> str:
    att, comp = _get(s, "passingAttempts"), _get(s, "passingCompletions")
    head = f"{_n(comp)}/{_n(att)}" if att else ""
    return _join([head, _bit(s, "passingYards", "yd"),
                  _bit(s, "passingTouchdowns", "TD"),
                  _bit(s, "passingInterceptions", "INT"),
                  _bit(s, "passingTimesSacked", "sk")])


def _rushing(s: Stats) -> str:
    return _join([_bit(s, "rushingAttempts", "car"),
                  _bit(s, "rushingYards", "yd"),
                  _bit(s, "rushingTouchdowns", "TD")])


def _receiving(s: Stats) -> str:
    rec, tgt = _get(s, "receivingReceptions"), _get(s, "receivingTargets")
    head = f"{_n(rec)}/{_n(tgt)} rec" if tgt else _bit(s, "receivingReceptions", "rec")
    return _join([head, _bit(s, "receivingYards", "yd"),
                  _bit(s, "receivingTouchdowns", "TD")])


def _returns(s: Stats) -> str:
    return _join([_bit(s, "kickoffReturnYards", "kr yd"),
                  _bit(s, "puntReturnYards", "pr yd")])


def _kicking(s: Stats) -> str:
    return _join([_ratio(s, "madeFieldGoals", "missedFieldGoals", "FG"),
                  _ratio(s, "madeExtraPoints", "missedExtraPoints", "XP")])


def _idp(s: Stats) -> str:
    return _join([_bit(s, "defensiveSoloTackles", "solo"),
                  _bit(s, "defensiveAssistedTackles", "ast"),
                  _bit(s, "defensiveSacks", "sk"),
                  _bit(s, "defensiveInterceptions", "INT"),
                  _bit(s, "defensivePassesDefensed", "PD"),
                  _bit(s, "defensiveForcedFumbles", "FF"),
                  _bit(s, "defensiveFumbles", "FR"),
                  _bit(s, "defensiveBlockedKicks", "BLK"),
                  _bit(s, "defensiveTouchdowns", "TD")])


def _team_defense(s: Stats) -> str:
    """A unit, not a man. Points and yards allowed are the headline, and the
    takeaways sit behind them, which is the order the fantasy scoring uses."""
    # Points allowed prints even at zero, unlike everything else on the page: a
    # shutout is the best thing a defense can do and dropping it as a falsy
    # number would hide the one line worth reading.
    pa = (f"{_n(_get(s, 'defensivePointsAllowed'))} PA"
          if s.get("defensivePointsAllowed") is not None else "")
    return _join([pa,
                  _bit(s, "defensiveYardsAllowed", "yd"),
                  _bit(s, "defensiveSacks", "sk"),
                  _bit(s, "defensiveInterceptions", "INT"),
                  _bit(s, "defensiveFumbles", "FR"),
                  _bit(s, "defensivePlusSpecialTeamsTouchdowns", "TD")])


def _is_unit(s: Stats) -> bool:
    """A team defense is the one thing that reports points and yards allowed, so
    the payload identifies itself and we do not have to trust a position
    string that spells the slot differently in every league."""
    return bool(s.get("defensivePointsAllowed") is not None
                or s.get("defensiveYardsAllowed") is not None)


def line(stats: Stats | None) -> str:
    """One line, or '' when there is nothing worth a line.

    Empty is the right answer more often than it looks: before kickoff the
    breakdown is all zeros, and a man who was active and did nothing measurable
    genuinely has no stat line. Both cases print nothing rather than a row of
    noughts.
    """
    if not stats:
        return ""
    if _is_unit(stats):
        return _team_defense(stats)
    phases = [_passing(stats), _rushing(stats), _receiving(stats),
              _kicking(stats), _idp(stats), _returns(stats),
              _bit(stats, "lostFumbles", "FL")]
    return " · ".join(p for p in phases if p)
