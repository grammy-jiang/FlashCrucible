"""JSONL logging utilities for tfqa.

Provides simple per-run JSONL logger suitable for tests and CLI.

API:
  - create_logger(run_id: str, log_dir: Path | None = None) -> Path
      Ensures log directory exists and returns path to JSONL file.
  - emit_event(run_id: str, event: dict, log_dir: Path | None = None) -> Path
      Append a single JSON event (with timestamp) to the run JSONL file.

The implementation is intentionally small and synchronous to be easy to test
and suitable for CLI usage. It writes newline-delimited JSON objects.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir


DEFAULT_APP_NAME = "tfqa"
DEFAULT_LOG_SUBDIR = "logs"


def _default_log_dir() -> Path:
    base = Path(user_data_dir(DEFAULT_APP_NAME))
    return base / DEFAULT_LOG_SUBDIR


def create_logger(run_id: str, log_dir: Path | None = None) -> Path:
    """Create log directory (if needed) and return run JSONL file path.

    Args:
        run_id: Run identifier (used in filename)
        log_dir: Optional override for log directory (for tests)

    Returns:
        Path to the JSONL logfile
    """
    if log_dir is None:
        log_dir = _default_log_dir()

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    filename = f"run-{run_id}.jsonl"
    file_path = log_dir / filename
    # Ensure file exists
    if not file_path.exists():
        file_path.touch()
    return file_path


def _timestamp_iso() -> str:
    # Use timezone-aware UTC timestamp
    from datetime import timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def emit_event(run_id: str, event: dict[str, Any], log_dir: Path | None = None) -> Path:
    """Append an event (dict) to the run JSONL logfile.

    The function will add/overwrite the `timestamp` and `run_id` keys to
    ensure consistent envelope fields.

    Args:
        run_id: Run identifier
        event: Dict payload for the event
        log_dir: Optional Path to override log directory

    Returns:
        Path to the logfile that was written
    """
    file_path = create_logger(run_id, log_dir=log_dir)

    envelope: dict[str, Any] = {
        "timestamp": _timestamp_iso(),
        "run_id": run_id,
    }
    # Merge event into envelope, without overwriting timestamp/run_id
    for k, v in event.items():
        if k in ("timestamp", "run_id"):
            continue
        envelope[k] = v

    line = json.dumps(envelope, default=str, ensure_ascii=False)

    # Write as newline-delimited JSON
    with file_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    return file_path
