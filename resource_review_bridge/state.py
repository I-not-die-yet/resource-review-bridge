"""Small durable job store with one-active-job admission."""

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


TERMINAL = {"completed", "partial", "blocked", "failed"}
STALE_RUNNING_SECONDS = 900


class BusyError(RuntimeError):
    pass


class JobStore:
    def __init__(self, path: str, retention_seconds: int = 86400):
        self.path = Path(path).expanduser()
        self.retention_seconds = retention_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                url_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.connection.close()

    def prune(self, now: Optional[int] = None) -> int:
        current = int(now or time.time())
        cursor = self.connection.execute(
            "DELETE FROM jobs WHERE status != 'running' AND updated_at < ?",
            (current - self.retention_seconds,),
        )
        return cursor.rowcount

    def claim(self, request_id: str, url_hash: str, now: Optional[int] = None) -> Tuple[str, Optional[Dict[str, Any]]]:
        current = int(now or time.time())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "UPDATE jobs SET status='failed', error_code='stale_interrupted', updated_at=? "
                "WHERE status='running' AND updated_at < ?",
                (current, current - STALE_RUNNING_SECONDS),
            )
            existing = self.connection.execute(
                "SELECT * FROM jobs WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing["url_hash"] != url_hash:
                    raise ValueError("request_id is already associated with another URL")
                self.connection.execute("COMMIT")
                return existing["job_id"], self._row(existing)
            active = self.connection.execute(
                "SELECT job_id FROM jobs WHERE status='running' LIMIT 1"
            ).fetchone()
            if active:
                raise BusyError("one resource review is already running")
            job_id = str(uuid.uuid4())
            self.connection.execute(
                "INSERT INTO jobs(job_id,request_id,url_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (job_id, request_id, url_hash, "running", current, current),
            )
            self.connection.execute("COMMIT")
            return job_id, None
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def latest_terminal(self) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            "SELECT job_id,request_id,status,error_code,created_at,updated_at "
            "FROM jobs WHERE status != 'running' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def finish(self, job_id: str, status: str, result: Optional[Dict[str, Any]], error_code: Optional[str] = None) -> None:
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        result_json = json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result is not None else None
        self.connection.execute(
            "UPDATE jobs SET status=?, result_json=?, error_code=?, updated_at=? WHERE job_id=? AND status='running'",
            (status, result_json, error_code, int(time.time()), job_id),
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        value = dict(row)
        value["result"] = json.loads(value.pop("result_json")) if value.get("result_json") else None
        return value
