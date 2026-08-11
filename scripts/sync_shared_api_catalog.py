#!/usr/bin/env python3
"""Copy only the allowed public API catalog fields into this project."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT.parent / "API资源库" / "data" / "resource-registry.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "shared-api-catalog.json"
ALLOWED_FIELDS = {
    "id",
    "name",
    "category",
    "purpose",
    "baseUrl",
    "docsUrl",
    "authLabel",
    "credentialEnv",
    "quotaNote",
}


def without_other_project_copy(value: str) -> str:
    parts = re.split(r"(?<=[。；;])", str(value or ""))
    return "".join(part for part in parts if "RWA" not in part).strip()


def build_catalog(payload: dict) -> dict:
    resources = []
    for resource in payload.get("resources", []):
        convexity = resource.get("consumers", {}).get("convexity", {})
        if convexity.get("status") not in {"active", "available", "planned"}:
            continue
        resources.append({
            "id": resource.get("id", ""),
            "name": resource.get("name", ""),
            "category": resource.get("category", ""),
            "purpose": convexity.get("usage") or without_other_project_copy(
                resource.get("purpose", "")
            ),
            "baseUrl": resource.get("baseUrl", ""),
            "docsUrl": resource.get("docsUrl", ""),
            "authLabel": resource.get("authLabel", ""),
            "credentialEnv": resource.get("credentialEnv", ""),
            "quotaNote": without_other_project_copy(resource.get("quotaNote", "")),
        })
    return {
        "schemaVersion": "m1.0-shared-api-catalog-v1",
        "sourceVersion": payload.get("version", ""),
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "boundary": (
            "Only API names, documentation, credential environment-variable locations, "
            "and total-quota notes are copied. Runtime state remains local."
        ),
        "allowedFields": sorted(ALLOWED_FIELDS),
        "resources": resources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    catalog = build_catalog(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
    temporary.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps({"resources": len(catalog["resources"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
