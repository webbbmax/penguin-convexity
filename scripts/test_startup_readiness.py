#!/usr/bin/env python3
import hashlib
import tempfile
from pathlib import Path

from init_db import initialize_database


ROOT = Path(__file__).resolve().parent.parent
SERVER_SOURCE = (ROOT / "scripts" / "serve_local.py").read_text(encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_startup_serves_before_rebuild():
    main_start = SERVER_SOURCE.index("def main():")
    main_end = SERVER_SOURCE.index("if __name__ == \"__main__\":")
    main_source = SERVER_SOURCE[main_start:main_end]
    assert "open_main_database_readonly()" in main_source
    assert "initialize_database(" not in main_source
    assert "initialize_update_recovery()" not in main_source
    assert main_source.index("ThreadingHTTPServer") < main_source.index(
        "target=rebuild_startup_snapshots"
    )
    assert "with QuietHandler.refresh_lock:" in SERVER_SOURCE
    startup_source = SERVER_SOURCE[
        SERVER_SOURCE.index("def rebuild_startup_snapshots():"):main_start
    ]
    assert "C21_STARTUP_SNAPSHOTS" in startup_source
    assert "rebuild_source_adapter_snapshot()" not in startup_source
    assert "rebuild_evidence_ledger_snapshot()" not in startup_source
    assert "startupRebuild" in SERVER_SOURCE


def test_startup_can_skip_existing_runtime_snapshot():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        db_path = root / "convexity.db"
        snapshot_path = root / "runtime-snapshot.js"
        initialize_database(db_path, snapshot_path, backup=False)
        before = digest(snapshot_path)
        initialize_database(
            db_path,
            snapshot_path,
            backup=False,
            refresh_snapshot=False,
        )
        assert digest(snapshot_path) == before


if __name__ == "__main__":
    test_startup_serves_before_rebuild()
    test_startup_can_skip_existing_runtime_snapshot()
    print("startup readiness checks passed")
