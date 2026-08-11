"""History/registry helpers for TFQA runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

DEFAULT_APP_NAME = "tfqa"
HISTORY_FILENAME = "history.jsonl"


def _default_history_path() -> Path:
    base = Path(user_data_dir(DEFAULT_APP_NAME))
    return base / HISTORY_FILENAME


def _ensure_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def record_run(
    *,
    command: str,
    run_id: str,
    device_path: str,
    status: str,
    message: str,
    stage_count: int,
    profile: str | None = None,
    metadata: dict[str, Any] | None = None,
    log_path: Path | None = None,
    history_file: Path | None = None,
) -> Path:
    """Append a single history entry for a run."""

    target_path = _default_history_path() if history_file is None else history_file
    _ensure_path(target_path)

    envelope: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "command": command,
        "device_path": device_path,
        "status": status,
        "message": message,
        "stage_count": stage_count,
        "profile": profile,
        "log_path": str(log_path) if log_path else None,
        "metadata": metadata or {},
    }

    with target_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")

    return target_path


def read_history(
    *, limit: int | None = None, history_file: Path | None = None
) -> list[dict[str, Any]]:
    """Read historical entries (newest first)."""

    target_path = _default_history_path() if history_file is None else history_file
    if not target_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    with target_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        return []

    entries = entries[-limit:] if limit and limit > 0 else entries
    return list(reversed(entries))
