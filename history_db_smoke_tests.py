from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from history_db import HistoryDB, redact_config, resolve_history_db_path


@dataclass
class FakeConfig:
    provider_name: str = "Test Provider"
    provider_type: str = "custom_openai_compatible"
    base_url: str = "http://localhost:8000/v1"
    model: str = "fake-model"
    api_key: str = "SECRET"
    custom_secret: str = "SECRET"
    access_token: str = "SECRET"
    story_prompt: str = "Do not store this prompt."
    output_file: str = "out.md"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "app_data" / "workstation_history.sqlite3"
        db = HistoryDB(db_path)
        assert db_path.exists()

        snapshot, snapshot_hash = redact_config(FakeConfig())
        assert snapshot_hash
        assert "SECRET" not in snapshot
        assert "Do not store this prompt" not in snapshot
        assert "fake-model" in snapshot

        config = FakeConfig()
        started = time.monotonic()
        run_id = db.start_run(
            "story",
            config,
            title="Smoke Story",
            output_file="out.md",
            prompt_tokens_est=10,
            completion_tokens_est=20,
            total_tokens_est=30,
        )
        assert run_id is not None
        db.add_run_file(run_id, "output", "out.md")
        db.add_provider_event(run_id, "test_connection", config, True, "ok", returned_model_count=2)
        db.finish_run(run_id, "completed", started)

        def worker_insert() -> None:
            worker_started = time.monotonic()
            worker_run_id = db.start_run("provider_test", config, title="Worker Thread")
            db.finish_run(worker_run_id, "completed", worker_started)

        thread = threading.Thread(target=worker_insert)
        thread.start()
        thread.join()
        db.close()

        conn = sqlite3.connect(db_path)
        try:
            run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            assert run_count == 2
            status = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            assert status == "completed"
            file_count = conn.execute("SELECT COUNT(*) FROM run_files").fetchone()[0]
            assert file_count == 1
            provider_count = conn.execute("SELECT COUNT(*) FROM provider_events").fetchone()[0]
            assert provider_count == 1
            schema_version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
            assert schema_version == "1"
        finally:
            conn.close()

        resolved = resolve_history_db_path(str(db_path))
        assert resolved == db_path

    print("history db smoke tests passed")


if __name__ == "__main__":
    main()
