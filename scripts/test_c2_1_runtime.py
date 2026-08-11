#!/usr/bin/env python3

import tempfile
from datetime import timedelta
from pathlib import Path

from c2_1_db import initialize_database, open_pipeline_db
from c2_1_resilience import commit_cursor
from c2_1_runtime import due_source_resume, interrupted_run_requires_resume, utc_now


def main():
    assert interrupted_run_requires_resume({"state": "running", "processId": 999999999}) is True
    assert interrupted_run_requires_resume({"state": "completed", "processId": 999999999}) is False
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pipeline.db"
        initialize_database(path)
        connection = open_pipeline_db(path)
        commit_cursor(connection, "source", "scope", "stage", "window", "source_failure", {"nextPage": 2})
        assert due_source_resume(now=utc_now(), db_path=path) is False
        assert due_source_resume(now=utc_now() + timedelta(minutes=16), db_path=path) is True
        connection.close()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pipeline.db"
        initialize_database(path)
        connection = open_pipeline_db(path)
        commit_cursor(connection, "source", "old-batch", "stage", "window-1", "source_failure", {"candidateIds": [1]})
        commit_cursor(connection, "source", "new-batch", "stage", "window-2", "success", {"candidateIds": [1]})
        connection.close()
        assert due_source_resume(now=utc_now() + timedelta(minutes=16), db_path=path) is False
        connection = open_pipeline_db(path)
        commit_cursor(connection, "source", "mixed-batch", "stage", "window-3", "source_failure", {"candidateIds": [1, 2]})
        commit_cursor(connection, "source", "partial-recovery", "stage", "window-4", "success", {"candidateIds": [1]})
        connection.close()
        assert due_source_resume(now=utc_now() + timedelta(minutes=16), db_path=path) is True
    print("C2.1 runtime tests passed")


if __name__ == "__main__":
    main()
