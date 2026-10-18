"""SQLite history of reviews, so a past run (including human fixes) can be reopened.

One row per run; the full result is kept as JSON. Uploaded files live next to the
database under ``uploads/<run_id>/`` so the original PDF can be opened again.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Store:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads = self.path.parent / "uploads"
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "id TEXT PRIMARY KEY, created_at TEXT, name TEXT, business TEXT, "
                "overall_status TEXT, fixes INTEGER, data TEXT)"
            )
            cols = [r[1] for r in c.execute("PRAGMA table_info(runs)")]
            if "deleted_at" not in cols:  # older databases: add the soft-delete column
                c.execute("ALTER TABLE runs ADD COLUMN deleted_at TEXT")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def upload_dir(self, run_id: str) -> Path:
        d = self.uploads / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _row(payload: dict) -> tuple:
        biz = payload["fields"]["business_name"]["effective_value"]
        fixes = sum(1 for f in payload["fields"].values() if f["override"] is not None)
        return payload["name"], biz, payload["overall_status"], fixes, json.dumps(payload)

    def save(self, payload: dict) -> dict:
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        name, biz, status, fixes, blob = self._row(payload)
        with self._conn() as c:
            c.execute("INSERT INTO runs (id, created_at, name, business, overall_status, fixes, data) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (payload["run_id"], payload["created_at"], name, biz, status, fixes, blob))
        return payload

    def save_pending(self, run_id: str, name: str) -> dict:
        """Insert a placeholder row (state QUEUED) so the review shows in the list while it waits."""
        created = datetime.now(timezone.utc).isoformat()
        payload = {"run_id": run_id, "name": name, "created_at": created, "state": "QUEUED"}
        with self._conn() as c:
            c.execute("INSERT INTO runs (id, created_at, name, business, overall_status, fixes, data) "
                      "VALUES (?,?,?,?,?,?,?)", (run_id, created, name, None, "QUEUED", 0, json.dumps(payload)))
        return payload

    def set_state(self, run_id: str, state: str, error: Optional[str] = None) -> None:
        """Move a placeholder run to PROCESSING or FAILED."""
        payload = self.get(run_id)
        if payload is None:
            return
        payload["state"] = state
        if error:
            payload["error"] = error
        with self._conn() as c:
            c.execute("UPDATE runs SET overall_status=?, data=? WHERE id=?", (state, json.dumps(payload), run_id))

    def fail_interrupted(self) -> None:
        """After a restart nothing is running, so queued or processing runs can never finish."""
        with self._conn() as c:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM runs WHERE overall_status IN ('QUEUED','PROCESSING')")]
        for run_id in ids:
            self.set_state(run_id, "FAILED", "The app was restarted before this review finished. Please upload it again.")

    def update(self, payload: dict) -> None:
        name, biz, status, fixes, blob = self._row(payload)
        with self._conn() as c:
            c.execute("UPDATE runs SET name=?, business=?, overall_status=?, fixes=?, data=? WHERE id=?",
                      (name, biz, status, fixes, blob, payload["run_id"]))

    def get(self, run_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, deleted: bool = False) -> list[dict]:
        where = "deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, created_at, name, business, overall_status, fixes "
                f"FROM runs WHERE {where} ORDER BY created_at DESC").fetchall()
        keys = ("run_id", "created_at", "name", "business", "overall_status", "fixes")
        return [dict(zip(keys, r)) for r in rows]

    def set_deleted(self, run_id: str, deleted: bool) -> bool:
        """Soft delete (or restore): the row and its files stay until purge()."""
        stamp = datetime.now(timezone.utc).isoformat() if deleted else None
        with self._conn() as c:
            return c.execute("UPDATE runs SET deleted_at=? WHERE id=?", (stamp, run_id)).rowcount > 0

    def purge(self, run_id: str) -> bool:
        """Delete forever: the row and the uploaded files."""
        with self._conn() as c:
            gone = c.execute("DELETE FROM runs WHERE id=?", (run_id,)).rowcount > 0
        shutil.rmtree(self.uploads / run_id, ignore_errors=True)
        return gone
