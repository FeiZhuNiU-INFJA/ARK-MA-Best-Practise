from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


CASE_DIR = Path(__file__).resolve().parents[1] / "cases" / "topic6"
spec = importlib.util.spec_from_file_location(
    "topic6_reset_pipeline_state", CASE_DIR / "reset_pipeline_state.py"
)
reset_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["topic6_reset_pipeline_state"] = reset_module
spec.loader.exec_module(reset_module)


def _create_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE pipeline_jobs (job_id TEXT PRIMARY KEY);
            CREATE TABLE pipeline_hc_events (
                id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES pipeline_jobs(job_id) ON DELETE CASCADE
            );
            CREATE TABLE user_oauth (open_id TEXT PRIMARY KEY);
            INSERT INTO pipeline_jobs VALUES ('job-1');
            INSERT INTO pipeline_hc_events VALUES (1, 'job-1');
            INSERT INTO user_oauth VALUES ('ou-user');
            """
        )
        connection.commit()
    finally:
        connection.close()


def _count(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def test_reset_clears_jobs_and_hc_events_but_preserves_oauth(tmp_path):
    path = tmp_path / "topic6_pipeline.db"
    _create_db(path)

    counts = reset_module.reset_pipeline_state(path)

    assert counts == {"jobs": 1, "hc_events": 1, "oauth": 1}
    assert _count(path, "pipeline_jobs") == 0
    assert _count(path, "pipeline_hc_events") == 0
    assert _count(path, "user_oauth") == 1


def test_reset_can_clear_oauth_when_explicitly_requested(tmp_path):
    path = tmp_path / "topic6_pipeline.db"
    _create_db(path)

    reset_module.reset_pipeline_state(path, include_oauth=True)

    assert _count(path, "user_oauth") == 0
