from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_HISTORY_DB_FILE = "app_data/workstation_history.sqlite3"
SECRET_MARKERS = ("key", "secret", "token", "password")
PROMPT_FIELDS = {
    "story_prompt",
    "system_prompt",
    "continuation_system_prompt",
    "rewrite_system_prompt",
    "translation_instruction_text",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_history_db_path(value: str | None) -> Path:
    raw = (value or DEFAULT_HISTORY_DB_FILE).strip() or DEFAULT_HISTORY_DB_FILE
    path = Path(raw)
    if not path.is_absolute():
        path = app_root() / path
    return path


def redact_config(config: Any) -> tuple[str, str]:
    if is_dataclass(config):
        data = asdict(config)
    elif isinstance(config, dict):
        data = dict(config)
    else:
        data = dict(getattr(config, "__dict__", {}))

    redacted: dict[str, Any] = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            continue
        if key in PROMPT_FIELDS:
            continue
        redacted[key] = value

    text = json.dumps(redacted, sort_keys=True, default=str, ensure_ascii=False)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


class HistoryDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.enabled = True
        self.warning: str | None = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def initialize(self) -> None:
        with self.lock:
            current = self.current_schema_version()
            if current is None:
                self.create_schema()
                self.set_meta("schema_version", str(SCHEMA_VERSION))
                self.set_meta("created_at", utc_now())
                self.set_meta("updated_at", utc_now())
                self.conn.commit()
                return

            if current > SCHEMA_VERSION:
                self.enabled = False
                self.warning = (
                    f"History DB schema version {current} is newer than supported version "
                    f"{SCHEMA_VERSION}; history writes disabled."
                )

    def current_schema_version(self) -> int | None:
        exists = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if not exists:
            return None
        row = self.conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if not row:
            return None
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO schema_meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_uuid TEXT NOT NULL UNIQUE,
                workflow_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                elapsed_seconds REAL,
                title TEXT,
                model TEXT,
                provider_name TEXT,
                provider_type TEXT,
                base_url TEXT,
                config_snapshot_json TEXT,
                config_snapshot_sha256 TEXT,
                input_file TEXT,
                output_file TEXT,
                report_file TEXT,
                working_dir TEXT,
                segments_dir TEXT,
                manifest_file TEXT,
                prompt_tokens_est INTEGER,
                completion_tokens_est INTEGER,
                total_tokens_est INTEGER,
                cost_base_est REAL,
                cost_recharge_est REAL,
                error_summary TEXT,
                notes TEXT
            );

            CREATE TABLE run_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                file_role TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT,
                size_bytes INTEGER,
                created_at TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
            );

            CREATE TABLE run_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                ordinal INTEGER,
                input_words INTEGER,
                output_words INTEGER,
                ratio REAL,
                status TEXT,
                finish_reason TEXT,
                validation_status TEXT,
                issue_count_error INTEGER DEFAULT 0,
                issue_count_warning INTEGER DEFAULT 0,
                source_file TEXT,
                output_file TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
            );

            CREATE TABLE validation_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                item_id TEXT,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                token TEXT,
                source_count INTEGER,
                output_count INTEGER,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
            );

            CREATE TABLE provider_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                provider_name TEXT,
                provider_type TEXT,
                base_url TEXT,
                model TEXT,
                success INTEGER NOT NULL,
                message TEXT,
                returned_model_count INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
            );
            """
        )

    def start_run(
        self,
        workflow_type: str,
        config: Any,
        title: str | None = None,
        input_file: str | None = None,
        output_file: str | None = None,
        report_file: str | None = None,
        working_dir: str | None = None,
        segments_dir: str | None = None,
        manifest_file: str | None = None,
        prompt_tokens_est: int | None = None,
        completion_tokens_est: int | None = None,
        total_tokens_est: int | None = None,
        cost_base_est: float | None = None,
        cost_recharge_est: float | None = None,
        notes: str | None = None,
    ) -> int | None:
        if not self.enabled:
            return None
        snapshot, snapshot_hash = redact_config(config)
        with self.lock:
            cursor = self.conn.execute(
                """
                INSERT INTO runs (
                    run_uuid, workflow_type, status, started_at, title, model, provider_name,
                    provider_type, base_url, config_snapshot_json, config_snapshot_sha256,
                    input_file, output_file, report_file, working_dir, segments_dir, manifest_file,
                    prompt_tokens_est, completion_tokens_est, total_tokens_est, cost_base_est,
                    cost_recharge_est, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    workflow_type,
                    "running",
                    utc_now(),
                    title,
                    getattr(config, "model", None),
                    getattr(config, "provider_name", None),
                    getattr(config, "provider_type", None),
                    getattr(config, "base_url", None),
                    snapshot,
                    snapshot_hash,
                    input_file,
                    output_file,
                    report_file,
                    working_dir,
                    segments_dir,
                    manifest_file,
                    prompt_tokens_est,
                    completion_tokens_est,
                    total_tokens_est,
                    cost_base_est,
                    cost_recharge_est,
                    notes,
                ),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int | None, status: str, started_monotonic: float, error_summary: str | None = None) -> None:
        if not self.enabled or run_id is None:
            return
        try:
            import time

            elapsed = time.monotonic() - started_monotonic
            with self.lock:
                self.conn.execute(
                    """
                    UPDATE runs
                    SET status=?, finished_at=?, elapsed_seconds=?, error_summary=?
                    WHERE id=?
                    """,
                    (status, utc_now(), elapsed, error_summary, run_id),
                )
                self.set_meta("updated_at", utc_now())
                self.conn.commit()
        except sqlite3.Error:
            self.enabled = False

    def add_run_file(self, run_id: int | None, file_role: str, path: str | None) -> None:
        if not self.enabled or run_id is None or not path:
            return
        file_path = Path(path)
        size = file_path.stat().st_size if file_path.exists() and file_path.is_file() else None
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO run_files (run_id, file_role, path, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, file_role, path, size, utc_now()),
            )
            self.conn.commit()

    def add_provider_event(
        self,
        run_id: int | None,
        event_type: str,
        config: Any,
        success: bool,
        message: str,
        returned_model_count: int | None = None,
    ) -> None:
        if not self.enabled or run_id is None:
            return
        with self.lock:
            self.conn.execute(
                """
                INSERT INTO provider_events (
                    run_id, event_type, provider_name, provider_type, base_url, model,
                    success, message, returned_model_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event_type,
                    getattr(config, "provider_name", None),
                    getattr(config, "provider_type", None),
                    getattr(config, "base_url", None),
                    getattr(config, "model", None),
                    1 if success else 0,
                    message,
                    returned_model_count,
                    utc_now(),
                ),
            )
            self.conn.commit()
