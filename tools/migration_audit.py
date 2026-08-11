#!/usr/bin/env python3
"""Read-only inventory for the M1.0 convexity migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def database_inventory(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {
            table: connection.execute(
                f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0]
            for table in tables
        }
        return {
            "relative_path": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_check": connection.execute("PRAGMA foreign_key_check").fetchall(),
            "table_count": len(tables),
            "row_counts": counts,
        }
    finally:
        connection.close()


def build_inventory(root: Path, include_hashes: bool) -> dict[str, object]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    extensions = Counter(path.suffix.lower() or "[no extension]" for path in files)
    database = root / "data" / "convexity.db"
    records = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        record: dict[str, object] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "modified_utc": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat(),
        }
        if include_hashes:
            record["sha256"] = sha256(path)
        records.append(record)
    return {
        "schema_version": "m1.0-inventory-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root.resolve()),
        "file_count": len(files),
        "directory_count": sum(1 for path in root.rglob("*") if path.is_dir()),
        "total_bytes": sum(record["bytes"] for record in records),
        "extension_counts": dict(sorted(extensions.items())),
        "database": database_inventory(database) if database.exists() else None,
        "files": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--hashes", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.root.resolve(), args.hashes)
    rendered = json.dumps(inventory, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
