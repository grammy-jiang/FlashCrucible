"""Run summary helpers for tfqa reporting."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, cast

from tfqa.core.logging import create_logger
from tfqa.core.models import TestStatus

_STATUS_PRIORITY: dict[TestStatus, int] = {
    "ok": 0,
    "warning": 1,
    "failed": 2,
    "error": 3,
}


def _aggregate_stage_status(summaries: list[dict[str, Any]]) -> TestStatus:
    highest: TestStatus = "ok"
    for summary in summaries:
        status = str(summary.get("status", "ok")).lower()
        if status in _STATUS_PRIORITY:
            stage_status = cast(TestStatus, status)
            if _STATUS_PRIORITY[stage_status] > _STATUS_PRIORITY[highest]:
                highest = stage_status
    return highest


def _load_events(log_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not log_path.exists():
        return events
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _filter_numeric_metrics(metrics: Any) -> dict[str, float | int]:
    if not isinstance(metrics, dict):
        return {}
    filtered: dict[str, float | int] = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            filtered[str(key)] = value
    return filtered


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _resolve_log_paths(
    run_id: str,
    log_dir: Path | None,
    log_path: Path | str | None,
    log_paths: Iterable[Path | str] | None,
) -> list[Path]:
    resolved: list[Path] = []
    if log_path is not None:
        resolved.append(Path(log_path))
    if log_paths is not None:
        for candidate in log_paths:
            resolved.append(Path(candidate))
    if not resolved:
        resolved.append(create_logger(run_id, log_dir=log_dir))
    return resolved


def _calculate_duration(events: list[dict[str, Any]]) -> float | None:
    if not events:
        return None
    start = _parse_timestamp(events[0].get("timestamp"))
    end = _parse_timestamp(events[-1].get("timestamp"))
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def summarize_run(
    run_id: str,
    log_dir: Path | None = None,
    log_path: Path | str | None = None,
    log_paths: Iterable[Path | str] | None = None,
) -> dict[str, Any]:
    """Summarize a run by aggregating the JSONL events emitted during execution."""

    candidates = _resolve_log_paths(run_id, log_dir, log_path, log_paths)
    events: list[dict[str, Any]] = []
    for candidate in candidates:
        events.extend(_load_events(candidate))
    if not events:
        raise ValueError(f"No events found for run_id {run_id}")
    events = sorted(
        events,
        key=lambda evt: (_parse_timestamp(evt.get("timestamp")) or datetime.min),
    )

    stage_events = [event for event in events if event.get("phase") == "pipeline"]
    stage_summaries: list[dict[str, Any]] = []
    metrics_by_stage: dict[str, dict[str, float | int]] = {}
    metrics_series_by_stage: dict[str, list[dict[str, Any]]] = {}
    time_series: list[dict[str, Any]] = []
    for event in stage_events:
        stage_summaries.append(
            {
                "stage": event.get("stage"),
                "status": event.get("status"),
                "metrics": event.get("metrics", {}),
                "timestamp": event.get("timestamp"),
            }
        )
        stage_name = event.get("stage") or event.get("phase") or "unknown"
        metrics = _filter_numeric_metrics(event.get("metrics", {}))
        if metrics:
            current = metrics_by_stage.setdefault(stage_name, {})
            current.update(metrics)
        metrics_series_by_stage.setdefault(stage_name, []).append(
            {
                "timestamp": event.get("timestamp"),
                "phase": event.get("phase"),
                "status": event.get("status"),
                "event_type": event.get("event_type"),
                "metrics": metrics,
            }
        )

    overall_status = _aggregate_stage_status(stage_summaries)

    for event in events:
        time_series.append(
            {
                "timestamp": event.get("timestamp"),
                "phase": event.get("phase"),
                "stage": event.get("stage"),
                "event_type": event.get("event_type"),
                "metrics": event.get("metrics", {}),
                "message": event.get("message"),
            }
        )

    duration_seconds = _calculate_duration(events)

    return {
        "run_id": run_id,
        "log_path": str(candidates[-1]),
        "event_count": len(events),
        "start": events[0].get("timestamp"),
        "end": events[-1].get("timestamp"),
        "duration_seconds": duration_seconds,
        "stage_summaries": stage_summaries,
        "overall_status": overall_status,
        "metrics_by_stage": metrics_by_stage,
        "metrics_series_by_stage": metrics_series_by_stage,
        "time_series": time_series,
        "events": events,
    }
