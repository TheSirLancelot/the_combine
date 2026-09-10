"""Waiver upgrades.

The naive version of this question returned 94 candidates in RCL, so almost
everything here is about what gets excluded and why.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combine.pipeline.calibration import Bias, Calibration
from combine.pipeline.waivers import Candidate, _synthetic, render


class FakePool:
    """Stands in for an ESPN pool player."""

    def __init__(self, name, pos, proj, week=1, slots=None, status="ACTIVE",
                 season=100.0):
        self.name = name
        self.position = pos
        self.proTeam = "KC"
        self.playerId = name
        self.injuryStatus = status
        self.eligibleSlots = slots if slots is not None else [pos, "BE"]
        self.projected_total_points = season
        self.stats = {week: {"projected_points": proj}}


def candidate(**kw):
    base = dict(league="rcl", week=1, name="Add", pos="DT", team="IND",
                week_proj=6.6, season_proj=90.0, drop_name="Drop", drop_pos="WR",
                drop_season_proj=100.0, week_gain=1.7, displaces="Starter")
    base.update(kw)
    return Candidate(**base)


def test_a_player_on_bye_is_not_a_candidate():
    """A zero projection is a bye or an inactive, not an upgrade."""
    assert _synthetic(FakePool("Resting", "WR", 0.0), 1) is None


def test_a_ruled_out_player_is_not_a_candidate():
    """Adding someone who cannot play this week achieves nothing."""
    assert _synthetic(FakePool("Hurt", "WR", 12.0, status="OUT"), 1) is None


def test_a_player_with_no_slot_data_is_not_a_candidate():
    assert _synthetic(FakePool("Mystery", "WR", 12.0, slots=[]), 1) is None


def test_a_missing_week_is_not_a_candidate():
    """Asked about week 3, given a player projected only for week 1."""
    assert _synthetic(FakePool("Later", "WR", 12.0, week=1), 3) is None


def test_synthetic_player_is_never_locked():
    """A pool player has not kicked off as far as this code is concerned, or the
    optimizer would refuse to move him in."""
    player = _synthetic(FakePool("Free", "WR", 9.0), 1)
    assert player is not None and not player.locked


def test_season_cost_is_reported_not_folded_into_the_gain():
    """Two currencies. A point this week that costs 90 points of season value is
    usually a bad trade and no single number says so."""
    c = candidate(season_proj=10.0, drop_season_proj=100.0)
    assert c.season_cost == -90.0
    assert c.trades_down
    assert "buys a week and pays for it later" in c.describe()


def test_an_add_that_also_helps_the_season_says_so():
    c = candidate(season_proj=150.0, drop_season_proj=100.0)
    assert not c.trades_down
    assert "gains 50 projected points of season value too" in c.describe()


def test_an_upward_correction_doing_the_work_is_disclosed():
    """Buckner projects below the starter and ranks first only because defensive
    tackles are corrected up 2.5 on 40 observations. Say so."""
    c = candidate(week_gain=1.7, correction=2.49, correction_n=40)
    assert c.correction_carries_it
    text = c.describe()
    assert "corrected UP by 2.5" in text and "40 player-weeks" in text


def test_a_downward_correction_survived_is_confidence_not_a_caveat():
    """He was marked down for his position and still qualifies, which is the
    opposite of a warning."""
    c = candidate(pos="CB", week_gain=1.6, correction=-1.71, correction_n=112)
    assert not c.correction_carries_it
    assert c.clears_despite_correction
    assert "marked DOWN 1.7" in c.describe()


def test_no_correction_means_no_note_at_all():
    c = candidate(pos="DE", week_gain=1.6, correction=0.0)
    text = c.describe()
    assert "NOTE" not in text and "marked DOWN" not in text


def test_nothing_available_says_so_plainly():
    text = render([], "A League", 1)
    assert "Nobody on the wire improves the lineup" in text
    assert "normal answer" in text


def test_calibration_can_reverse_a_raw_comparison():
    """The whole reason calibration comes first: on raw numbers the corner beats
    the tackle, and corrected he does not."""
    cal = Calibration("rcl", 2025, {
        "CB": Bias("CB", 112, -1.71, 0.51),
        "DT": Bias("DT", 40, 2.49, 0.98),
    })
    assert 10.0 > 6.6                                  # raw
    assert cal.adjust("CB", 10.0) < cal.adjust("DT", 6.6) + 0.8
    assert round(cal.adjust("DT", 6.6), 2) == 9.09
