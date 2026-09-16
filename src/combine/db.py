"""SQLite access. WAL so the pipeline can write while MCP tools read."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .config import DB_PATH

SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH, *, readonly: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if readonly and path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    else:
        conn = sqlite3.connect(path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after a table shipped. `CREATE TABLE IF NOT EXISTS` does
# nothing to a table that already exists, so a new column has to be added
# explicitly or every existing database silently lacks it.
ADDED_COLUMNS = {
    "recommendation": {"taken": "INTEGER"},
}


def migrate(conn) -> list[str]:
    """Add columns the schema gained since this database was created."""
    done = []
    known = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, columns in ADDED_COLUMNS.items():
        if table not in known:
            continue          # the schema script will create it, with the column
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                done.append(f"{table}.{name}")
    if done:
        conn.commit()
    return done


def ensure_schema(path: Path = DB_PATH) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA.read_text())
        migrate(conn)


@contextmanager
def job(name: str):
    """Wrap a scheduled job so a stale table is distinguishable from a job that never fired."""
    conn = connect()
    cur = conn.execute(
        "INSERT INTO run_log (job, started_at, status) VALUES (?,?,'running')", (name, now())
    )
    run_id = cur.lastrowid
    conn.commit()
    try:
        yield conn
    except Exception as exc:
        conn.execute(
            "UPDATE run_log SET finished_at=?, status='error', detail=? WHERE id=?",
            (now(), f"{type(exc).__name__}: {exc}"[:500], run_id),
        )
        conn.commit()
        raise
    else:
        conn.execute(
            "UPDATE run_log SET finished_at=?, status='ok' WHERE id=?", (now(), run_id)
        )
        conn.commit()
    finally:
        conn.close()
