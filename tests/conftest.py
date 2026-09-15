"""Keep the test suite out of the real database.

Found the hard way: `bot.build_waivers` records every recommendation it makes,
and a test that exercised it wrote a fixture player into William's production
scorecard. "D. Buckner" and "A. Starter" in week 3 are test data, and they were
sitting in the same table the app reads.

This points every test at a throwaway file for the whole session, so no test can
reach the real one whether or not it thinks about the database at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True, scope="session")
def _never_the_real_database(tmp_path_factory):
    from combine import config, db

    scratch = tmp_path_factory.mktemp("combine-db") / "test.db"
    real_config, real_db = config.DB_PATH, db.DB_PATH
    config.DB_PATH = scratch
    db.DB_PATH = scratch
    db.connect.__defaults__ = (scratch,)
    db.ensure_schema(scratch)
    yield scratch
    config.DB_PATH, db.DB_PATH = real_config, real_db
