#!/usr/bin/env python3
"""Safely retain and remove registered development-only artifacts.

Automatic deletion is structurally limited to direct child directories of
``runtime/temp-artifacts``.  Product databases, release baselines, backups and
other runtime directories cannot be registered or deleted by this module.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANAGED_ROOT = PROJECT_ROOT / "runtime" / "temp-artifacts"
STATE_PATH = PROJECT_ROOT / "runtime" / "maintenance" / "temp-artifact-sweep.json"
AUDIT_PATH = PROJECT_ROOT / "runtime" / "maintenance" / "temp-artifact-cleanup.jsonl"
MARKER_NAME = ".retention.json"
SCHEMA_VERSION = "convexity-temp-artifact-v1"
SWEEP_SCHEMA_VERSION = "convexity-temp-artifact-sweep-v1"
DEFAULT_RETENTION_HOURS = 24.0
MAX_RETENTION_HOURS = 720.0
ABANDONED_GRACE_HOURS = 24.0
ACTIVE_QUIET_MINUTES = 60.0


class RetentionError(RuntimeError):
    """The requested operation crossed a retention safety boundary."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise RetentionError("invalid retention timestamp") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise RetentionError(f"cannot read retention metadata: {path}") from error
    if not isinstance(value, dict):
        raise RetentionError(f"retention metadata is not an object: {path}")
    return value


def pid_is_running(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, value
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & 0x400)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-")
    return (cleaned or "task")[:48]


def _validate_hours(value: float, *, allow_zero: bool = False) -> float:
    hours = float(value)
    lower_bound = 0.0 if allow_zero else 0.01
    if hours < lower_bound or hours > MAX_RETENTION_HOURS:
        raise RetentionError(
            f"retention hours must be between {lower_bound} and {MAX_RETENTION_HOURS}"
        )
    return hours


class TempArtifactRetention:
    """Create, seal, inspect and sweep development-only artifact directories."""

    def __init__(
        self,
        *,
        project_root: Path = PROJECT_ROOT,
        managed_root: Path = MANAGED_ROOT,
        state_path: Path = STATE_PATH,
        audit_path: Path = AUDIT_PATH,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.managed_root = Path(managed_root).resolve()
        self.state_path = Path(state_path).resolve()
        self.audit_path = Path(audit_path).resolve()
        self.clock = clock
        runtime_root = (self.project_root / "runtime").resolve()
        if self.managed_root.parent != runtime_root:
            raise RetentionError("managed root must be a direct child of project runtime")
        if self.managed_root.name != "temp-artifacts":
            raise RetentionError("managed root must be runtime/temp-artifacts")
        for metadata_path in (self.state_path, self.audit_path):
            try:
                metadata_path.relative_to(runtime_root)
            except ValueError as error:
                raise RetentionError("retention metadata must stay inside project runtime") from error

    def _artifact_dir(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        resolved = candidate.resolve(strict=False)
        if resolved.parent != self.managed_root or resolved == self.managed_root:
            raise RetentionError(
                "automatic cleanup only accepts direct children of runtime/temp-artifacts"
            )
        return resolved

    def _marker_path(self, artifact_dir: Path) -> Path:
        return artifact_dir / MARKER_NAME

    def _relative_path(self, artifact_dir: Path) -> str:
        try:
            return artifact_dir.relative_to(self.project_root).as_posix()
        except ValueError as error:
            raise RetentionError("artifact path is outside the project") from error

    def create(
        self,
        *,
        owner_task: str,
        purpose: str,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
        owner_pid: int | None = None,
        now: datetime | None = None,
    ) -> dict:
        current = now or self.clock()
        hours = _validate_hours(retention_hours)
        self.managed_root.mkdir(parents=True, exist_ok=True)
        artifact_id = (
            f"{_slug(owner_task)}-{current.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{uuid.uuid4().hex[:8]}"
        )
        artifact_dir = self._artifact_dir(self.managed_root / artifact_id)
        artifact_dir.mkdir()
        return self.register(
            artifact_dir,
            owner_task=owner_task,
            purpose=purpose,
            retention_hours=hours,
            owner_pid=owner_pid,
            now=current,
            artifact_id=artifact_id,
        )

    def register(
        self,
        path: Path | str,
        *,
        owner_task: str,
        purpose: str,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
        owner_pid: int | None = None,
        now: datetime | None = None,
        artifact_id: str | None = None,
    ) -> dict:
        current = now or self.clock()
        hours = _validate_hours(retention_hours)
        artifact_dir = self._artifact_dir(path)
        if not artifact_dir.is_dir():
            raise RetentionError("registered artifact must be an existing directory")
        artifact_stat = os.lstat(artifact_dir)
        if _is_reparse(artifact_stat):
            raise RetentionError("reparse points and symbolic links cannot be registered")
        marker_path = self._marker_path(artifact_dir)
        if marker_path.exists():
            raise RetentionError("artifact directory is already registered")
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "artifactId": artifact_id or artifact_dir.name,
            "relativePath": self._relative_path(artifact_dir),
            "ownerTask": str(owner_task).strip(),
            "purpose": str(purpose).strip(),
            "state": "active",
            "ownerPid": int(owner_pid if owner_pid is not None else os.getpid()),
            "registeredAt": iso_time(current),
            "deleteAfter": iso_time(current + timedelta(hours=hours)),
            "sealedAt": None,
            "sealedPayloadMtimeNs": None,
        }
        if not payload["ownerTask"] or not payload["purpose"]:
            raise RetentionError("owner task and purpose are required")
        atomic_json(marker_path, payload)
        return {**payload, "absolutePath": str(artifact_dir)}

    def _load_marker(self, artifact_dir: Path) -> dict:
        marker = load_json(self._marker_path(artifact_dir))
        if marker.get("schemaVersion") != SCHEMA_VERSION:
            raise RetentionError("unknown or missing retention marker schema")
        if marker.get("relativePath") != self._relative_path(artifact_dir):
            raise RetentionError("retention marker path does not match its directory")
        if marker.get("state") not in {"active", "sealed"}:
            raise RetentionError("retention marker has an unsupported state")
        parse_time(marker.get("deleteAfter"))
        return marker

    def _inspect_payload(self, artifact_dir: Path) -> dict:
        logical_bytes = 0
        latest_mtime_ns = 0
        stack = [artifact_dir]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError as error:
                raise RetentionError(f"cannot inspect artifact directory: {current}") from error
            for entry in entries:
                if entry.name == MARKER_NAME and Path(entry.path).parent == artifact_dir:
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise RetentionError(f"cannot inspect artifact entry: {entry.path}") from error
                if _is_reparse(entry_stat):
                    raise RetentionError("artifact contains a reparse point or symbolic link")
                latest_mtime_ns = max(latest_mtime_ns, int(entry_stat.st_mtime_ns))
                if stat.S_ISDIR(entry_stat.st_mode):
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(entry_stat.st_mode):
                    logical_bytes += int(entry_stat.st_size)
                else:
                    raise RetentionError("artifact contains an unsupported filesystem entry")
        return {
            "logicalBytes": logical_bytes,
            "latestPayloadMtimeNs": latest_mtime_ns,
        }

    def seal(
        self,
        path: Path | str,
        *,
        retention_hours: float = DEFAULT_RETENTION_HOURS,
        now: datetime | None = None,
    ) -> dict:
        current = now or self.clock()
        hours = _validate_hours(retention_hours, allow_zero=True)
        artifact_dir = self._artifact_dir(path)
        marker = self._load_marker(artifact_dir)
        inspection = self._inspect_payload(artifact_dir)
        marker.update(
            {
                "state": "sealed",
                "ownerPid": None,
                "sealedAt": iso_time(current),
                "deleteAfter": iso_time(current + timedelta(hours=hours)),
                "sealedPayloadMtimeNs": inspection["latestPayloadMtimeNs"],
                "logicalBytesAtSeal": inspection["logicalBytes"],
            }
        )
        atomic_json(self._marker_path(artifact_dir), marker)
        return {**marker, "absolutePath": str(artifact_dir)}

    def _load_sweep_state(self) -> dict:
        state = load_json(self.state_path)
        if state and state.get("schemaVersion") != SWEEP_SCHEMA_VERSION:
            raise RetentionError("unknown sweep state schema")
        return state

    def _write_audit(self, payload: dict) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def sweep(
        self,
        *,
        min_interval_hours: float = 24.0,
        force: bool = False,
        now: datetime | None = None,
    ) -> dict:
        current = now or self.clock()
        interval = _validate_hours(min_interval_hours, allow_zero=True)
        previous = self._load_sweep_state()
        if not force and previous.get("lastSweepAt"):
            elapsed = current - parse_time(previous["lastSweepAt"])
            if elapsed < timedelta(hours=interval):
                return {
                    "status": "skipped_recently",
                    "lastSweepAt": previous["lastSweepAt"],
                    "nextSweepAfter": iso_time(
                        parse_time(previous["lastSweepAt"]) + timedelta(hours=interval)
                    ),
                }

        self.managed_root.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": "completed",
            "sweepAt": iso_time(current),
            "deletedArtifacts": 0,
            "deletedLogicalBytes": 0,
            "pendingArtifacts": 0,
            "inUseArtifacts": 0,
            "postponedArtifacts": 0,
            "blockedArtifacts": 0,
            "unregisteredEntries": 0,
            "deleted": [],
            "blocked": [],
        }
        for entry in sorted(self.managed_root.iterdir(), key=lambda value: value.name):
            try:
                artifact_dir = self._artifact_dir(entry)
                if not artifact_dir.is_dir() or not self._marker_path(artifact_dir).is_file():
                    summary["unregisteredEntries"] += 1
                    continue
                marker = self._load_marker(artifact_dir)
                delete_after = parse_time(marker["deleteAfter"])
                if current < delete_after:
                    summary["pendingArtifacts"] += 1
                    continue
                if marker["state"] == "active" and pid_is_running(marker.get("ownerPid")):
                    summary["inUseArtifacts"] += 1
                    continue
                inspection = self._inspect_payload(artifact_dir)
                latest_mtime_ns = inspection["latestPayloadMtimeNs"]
                if marker["state"] == "sealed":
                    sealed_mtime_ns = int(marker.get("sealedPayloadMtimeNs") or 0)
                    if latest_mtime_ns > sealed_mtime_ns:
                        raise RetentionError("sealed artifact changed after it was sealed")
                elif latest_mtime_ns:
                    latest = datetime.fromtimestamp(
                        latest_mtime_ns / 1_000_000_000, tz=timezone.utc
                    )
                    if latest > delete_after or current - latest < timedelta(
                        minutes=ACTIVE_QUIET_MINUTES
                    ):
                        marker["deleteAfter"] = iso_time(
                            current + timedelta(hours=ABANDONED_GRACE_HOURS)
                        )
                        marker["ownerPid"] = None
                        marker["postponedAt"] = iso_time(current)
                        atomic_json(self._marker_path(artifact_dir), marker)
                        summary["postponedArtifacts"] += 1
                        continue

                # Re-resolve the exact registered target immediately before deletion.
                artifact_dir = self._artifact_dir(artifact_dir)
                marker_check = self._load_marker(artifact_dir)
                if marker_check.get("artifactId") != marker.get("artifactId"):
                    raise RetentionError("retention marker changed during sweep")
                shutil.rmtree(artifact_dir)
                summary["deletedArtifacts"] += 1
                summary["deletedLogicalBytes"] += inspection["logicalBytes"]
                summary["deleted"].append(
                    {
                        "artifactId": marker["artifactId"],
                        "relativePath": marker["relativePath"],
                        "logicalBytes": inspection["logicalBytes"],
                    }
                )
            except RetentionError as error:
                summary["blockedArtifacts"] += 1
                summary["blocked"].append(
                    {"entry": entry.name, "reason": str(error)}
                )
            except OSError as error:
                summary["blockedArtifacts"] += 1
                summary["blocked"].append(
                    {"entry": entry.name, "reason": f"filesystem error: {error}"}
                )

        state = {
            "schemaVersion": SWEEP_SCHEMA_VERSION,
            "lastSweepAt": summary["sweepAt"],
            "lastResult": summary,
        }
        atomic_json(self.state_path, state)
        self._write_audit(summary)
        return summary

    def status(self, *, now: datetime | None = None) -> dict:
        current = now or self.clock()
        self.managed_root.mkdir(parents=True, exist_ok=True)
        result = {
            "schemaVersion": SWEEP_SCHEMA_VERSION,
            "managedRoot": str(self.managed_root),
            "lastSweep": self._load_sweep_state().get("lastResult"),
            "registered": 0,
            "due": 0,
            "inUse": 0,
            "blocked": 0,
            "unregistered": 0,
        }
        for entry in self.managed_root.iterdir():
            try:
                artifact_dir = self._artifact_dir(entry)
                if not artifact_dir.is_dir() or not self._marker_path(artifact_dir).is_file():
                    result["unregistered"] += 1
                    continue
                marker = self._load_marker(artifact_dir)
                result["registered"] += 1
                if current >= parse_time(marker["deleteAfter"]):
                    result["due"] += 1
                if marker["state"] == "active" and pid_is_running(marker.get("ownerPid")):
                    result["inUse"] += 1
            except (OSError, RetentionError):
                result["blocked"] += 1
        return result


@contextmanager
def managed_temp_artifact(
    *,
    owner_task: str,
    purpose: str,
    active_ttl_hours: float = 72.0,
    retention_after_finish_hours: float = DEFAULT_RETENTION_HOURS,
    manager: TempArtifactRetention | None = None,
) -> Iterator[Path]:
    """Give a producer a managed directory and seal it even when work fails."""

    retention = manager or TempArtifactRetention()
    marker = retention.create(
        owner_task=owner_task,
        purpose=purpose,
        retention_hours=active_ttl_hours,
    )
    artifact_dir = Path(marker["absolutePath"])
    try:
        yield artifact_dir
    finally:
        retention.seal(
            artifact_dir,
            retention_hours=retention_after_finish_hours,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Managed temporary artifact retention")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--owner-task", required=True)
    create_parser.add_argument("--purpose", required=True)
    create_parser.add_argument("--retention-hours", type=float, default=DEFAULT_RETENTION_HOURS)
    create_parser.add_argument("--owner-pid", type=int)

    register_parser = subparsers.add_parser("register")
    register_parser.add_argument("--path", required=True)
    register_parser.add_argument("--owner-task", required=True)
    register_parser.add_argument("--purpose", required=True)
    register_parser.add_argument("--retention-hours", type=float, default=DEFAULT_RETENTION_HOURS)
    register_parser.add_argument("--owner-pid", type=int)

    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("--path", required=True)
    seal_parser.add_argument("--retention-hours", type=float, default=DEFAULT_RETENTION_HOURS)

    sweep_parser = subparsers.add_parser("sweep")
    sweep_parser.add_argument("--min-interval-hours", type=float, default=24.0)
    sweep_parser.add_argument("--force", action="store_true")

    subparsers.add_parser("status")
    args = parser.parse_args()
    manager = TempArtifactRetention()
    try:
        if args.command == "create":
            result = manager.create(
                owner_task=args.owner_task,
                purpose=args.purpose,
                retention_hours=args.retention_hours,
                owner_pid=args.owner_pid,
            )
        elif args.command == "register":
            result = manager.register(
                args.path,
                owner_task=args.owner_task,
                purpose=args.purpose,
                retention_hours=args.retention_hours,
                owner_pid=args.owner_pid,
            )
        elif args.command == "seal":
            result = manager.seal(
                args.path,
                retention_hours=args.retention_hours,
            )
        elif args.command == "sweep":
            result = manager.sweep(
                min_interval_hours=args.min_interval_hours,
                force=args.force,
            )
        else:
            result = manager.status()
    except RetentionError as error:
        result = {"status": "blocked", "error": str(error)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
