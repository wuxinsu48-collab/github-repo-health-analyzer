from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "reports.sqlite3"


def get_db_path() -> Path:
    configured = os.getenv("GITHUB_ANALYSIS_DB")
    return Path(configured) if configured else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_full_name TEXT NOT NULL,
                repo_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                final_score INTEGER NOT NULL,
                rule_score INTEGER NOT NULL,
                ai_score INTEGER,
                payload TEXT NOT NULL
            )
            """
        )
        conn.commit()


def insert_report(payload: dict[str, Any]) -> dict[str, Any]:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    repo = payload["evidence"]["repo"]
    ai = payload.get("ai_assessment")
    ai_score = ai.get("ai_score") if isinstance(ai, dict) else None
    record_payload = {**payload, "created_at": created_at}

    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reports (repo_full_name, repo_url, created_at, final_score, rule_score, ai_score, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo["full_name"],
                repo["html_url"],
                created_at,
                payload["final_score"],
                payload["rule_score"]["rule_score"],
                ai_score,
                json.dumps(record_payload, ensure_ascii=False),
            ),
        )
        conn.commit()
        report_id = int(cursor.lastrowid)

    return get_report(report_id)


def _row_to_report(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(row["payload"])
    return {
        "id": row["id"],
        "repo_full_name": row["repo_full_name"],
        "repo_url": row["repo_url"],
        "created_at": row["created_at"],
        "final_score": row["final_score"],
        "rule_score": row["rule_score"],
        "ai_score": row["ai_score"],
        "payload": payload,
    }


def get_report(report_id: int) -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        raise KeyError(f"report {report_id} not found")
    return _row_to_report(row)


def list_reports(limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, repo_full_name, repo_url, created_at, final_score, rule_score, ai_score, payload
            FROM reports
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_report(row) for row in rows]

