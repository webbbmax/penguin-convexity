#!/usr/bin/env python3

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from c2_1_db import initialize_database, open_pipeline_db
from c2_1_resilience import commit_cursor, cursor_decision


def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pipeline.db"
        initialize_database(path)
        connection = open_pipeline_db(path)
        assert cursor_decision(connection, "source", "scope", "stage", "window")["action"] == "run"

        commit_cursor(connection, "source", "scope", "stage", "window", "source_failure", {"nextPage": 4})
        cooling = cursor_decision(connection, "source", "scope", "stage", "window")
        assert cooling["action"] == "cooldown" and cooling["cursor"]["nextPage"] == 4
        row = connection.execute("SELECT consecutive_failures,next_retry_at FROM source_cursors").fetchone()
        assert row["consecutive_failures"] == 1 and row["next_retry_at"]

        after_cooldown = datetime.now(timezone.utc) + timedelta(minutes=16)
        assert cursor_decision(connection, "source", "scope", "stage", "window", now=after_cooldown)["action"] == "run"
        commit_cursor(connection, "source", "scope", "stage", "window", "success", {"nextPage": 5})
        assert cursor_decision(connection, "source", "scope", "stage", "window")["action"] == "complete"
        row = connection.execute("SELECT consecutive_failures,next_retry_at,last_success_at FROM source_cursors").fetchone()
        assert row["consecutive_failures"] == 0 and row["next_retry_at"] is None and row["last_success_at"]

        # A new collection window is allowed; an old completed fragment is not requested again.
        assert cursor_decision(connection, "source", "scope", "stage", "next-window")["action"] == "run"
        connection.close()
    print("C2.1 resilience tests passed")


if __name__ == "__main__":
    main()
