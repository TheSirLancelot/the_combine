"""The stat line.

Every breakdown in here is a real one, copied from the live probe against the
two leagues rather than invented, because the failure mode this guards against
is a key that ESPN spells differently from the way we guessed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.statline import line


def test_a_quarterback_reads_passing_first_then_what_he_ran():
    """Matthew Stafford, RCL week 1, 4.0 points."""
    assert line({
        "passingAttempts": 25.0, "passingCompletions": 15.0,
        "passingIncompletions": 10.0, "passingYards": 155.0,
        "passingInterceptions": 1.0, "passingCompletionPercentage": 60.0,
        "rushingAttempts": 2.0, "rushingYards": -1.0,
        "rushingYardsPerAttempt": -0.5, "turnovers": 1.0, "teamLoss": 1.0,
        "210": 1.0, "211": 5.0,
    }) == "15/25, 155 yd, 1 INT · 2 car, -1 yd"


def test_a_back_who_caught_passes_gets_both_phases():
    """Jahmyr Gibbs, RCL week 1, 33.5 points."""
    assert line({
        "rushingAttempts": 29.0, "rushingYards": 156.0, "rushingTouchdowns": 2.0,
        "receivingReceptions": 5.0, "receivingTargets": 5.0,
        "receivingYards": 30.0, "lostFumbles": 1.0,
    }) == "29 car, 156 yd, 2 TD · 5/5 rec, 30 yd · 1 FL"


def test_a_linebacker_reads_in_tackles():
    """Alex Singleton, RCL week 1, 12.5 points."""
    assert line({
        "defensiveAssistedTackles": 10.0, "defensiveSoloTackles": 5.0,
        "defensiveTotalTackles": 15.0, "teamLoss": 1.0, "110": 5.0,
    }) == "5 solo, 10 ast"


def test_a_team_defense_leads_with_what_it_gave_up():
    assert line({
        "defensivePointsAllowed": 17.0, "defensiveYardsAllowed": 312.0,
        "defensiveSacks": 3.0, "defensiveInterceptions": 1.0,
    }) == "17 PA, 312 yd, 3 sk, 1 INT"


def test_a_kicker_reads_as_made_over_attempted():
    """ESPN sends made and missed, never attempts, so the denominator is the
    two of them added up — still only its own numbers."""
    assert line({"madeFieldGoals": 2.0, "missedFieldGoals": 1.0,
                 "madeExtraPoints": 3.0}) == "2/3 FG, 3/3 XP"


def test_numeric_stat_ids_are_dropped_rather_than_guessed_at():
    """A wrong label on a number is worse than no number."""
    assert line({"210": 1.0, "211": 5.0, "213": 3.0}) == ""


def test_derived_figures_never_appear():
    """ESPN sends a completion percentage and a yards per carry. Both are
    arithmetic on numbers already on the line, and neither is in a box score."""
    out = line({"rushingAttempts": 10.0, "rushingYards": 45.0,
                "rushingYardsPerAttempt": 4.5})
    assert out == "10 car, 45 yd"


def test_a_man_who_did_nothing_measurable_gets_no_line():
    """Myles Garrett, RCL week 1, 0.0 points: he played and nothing scored."""
    assert line({"teamWin": 1.0, "210": 1.0}) == ""
    assert line({}) == ""
    assert line(None) == ""


def test_zeros_are_absent_rather_than_printed():
    """'0 TD' on every line is what makes the line saying '2 TD' hard to find."""
    assert line({"receivingReceptions": 4.0, "receivingTargets": 6.0,
                 "receivingYards": 51.0, "receivingTouchdowns": 0.0}
                ) == "4/6 rec, 51 yd"


def test_whole_numbers_print_whole():
    assert "155 yd" in line({"passingYards": 155.0, "passingAttempts": 1.0})


def test_a_shutout_still_reports_the_points_allowed():
    """Zero points allowed is the best thing a defense can do, and the one zero
    on the whole page that has to survive."""
    assert line({"defensivePointsAllowed": 0.0, "defensiveYardsAllowed": 180.0,
                 "defensiveSacks": 4.0}) == "0 PA, 180 yd, 4 sk"


def test_receiving_without_targets_still_reports_catches():
    assert line({"receivingReceptions": 3.0, "receivingYards": 28.0}
                ) == "3 rec, 28 yd"
