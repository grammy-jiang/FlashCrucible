"""Trend aggregation helpers for TFQA history data."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, cast


def _normalize_stage(stage: str | None) -> str:
    if not stage:
        return ""
    return stage.strip().lower()


def _matches_stage(stage_name: str, stage_filter: str) -> bool:
    normalized_name = stage_name.lower()
    normalized_filter = stage_filter.lower().strip()
    if normalized_name == normalized_filter:
        return True
    short_name = normalized_name.split(".")[-1]
    return short_name == normalized_filter


def _numeric_metrics(metrics: Mapping[str, Any] | None) -> dict[str, float | int]:
    if metrics is None:
        return {}
    filtered: dict[str, float | int] = {}
    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            filtered[str(key)] = value
    return filtered


def _stage_entries(metadata: Mapping[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    processed: set[str] = set()
    details = metadata.get("stage_details")
    if isinstance(details, dict):
        for stage_name, detail in cast(dict[str, Any], details).items():
            if not isinstance(detail, dict):
                continue
            processed.add(stage_name)
            yield stage_name, cast(dict[str, Any], detail)
    metrics_map = metadata.get("metrics")
    if isinstance(metrics_map, dict):
        for stage_name, metrics in cast(dict[str, Any], metrics_map).items():
            if stage_name in processed:
                continue
            yield stage_name, {"metrics": metrics}


def _coerce_duration(value: Any | None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _collect_stage_summaries(
    entries: list[dict[str, Any]], stage_filter: str | None
) -> dict[str, dict[str, Any]]:
    stage_summaries: dict[str, dict[str, Any]] = {}
    for entry in entries:
        metadata = entry.get("metadata")
        if not isinstance(metadata, dict):
            continue
        metadata = cast(dict[str, Any], metadata)
        for stage_name, detail in _stage_entries(metadata):
            if stage_filter and not _matches_stage(stage_name, stage_filter):
                continue
            numeric_metrics = _numeric_metrics(
                cast(Mapping[str, Any] | None, detail.get("metrics"))
            )
            duration = _coerce_duration(detail.get("duration_seconds"))
            status = detail.get("status")
            _accumulate_stage_metrics(
                stage_summaries,
                stage_name,
                numeric_metrics,
                duration=duration,
                status=status,
            )
    return stage_summaries


def _accumulate_stage_metrics(
    stage_summaries: dict[str, dict[str, Any]],
    stage_name: str,
    numeric_metrics: dict[str, float | int],
    *,
    duration: float | None,
    status: Any | None,
) -> None:
    summary = stage_summaries.setdefault(
        stage_name,
        {
            "count": 0,
            "occurrences": 0,
            "totals": {},
            "last_metrics": {},
            "status_counts": {},
            "duration_count": 0,
            "duration_total": 0.0,
            "duration_min": None,
            "duration_max": None,
            "duration_last": None,
        },
    )
    summary["occurrences"] += 1
    if numeric_metrics:
        summary["count"] += 1
        for metric_key, metric_value in numeric_metrics.items():
            summary["totals"][metric_key] = (
                summary["totals"].get(metric_key, 0) + metric_value
            )
            summary["last_metrics"][metric_key] = metric_value
    if status is not None:
        key = str(status).lower()
        summary["status_counts"][key] = summary["status_counts"].get(key, 0) + 1
    if duration is not None:
        summary["duration_count"] += 1
        summary["duration_total"] += duration
        if summary["duration_min"] is None or duration < summary["duration_min"]:
            summary["duration_min"] = duration
        if summary["duration_max"] is None or duration > summary["duration_max"]:
            summary["duration_max"] = duration
        summary["duration_last"] = duration


def _summaries_to_aggregated(
    stage_summaries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    for stage_name, summary in stage_summaries.items():
        count = summary["count"]
        totals: dict[str, float] = {}
        averages: dict[str, float] = {}
        for key, total in summary["totals"].items():
            averaged = total / count
            totals[key] = total
            averages[key] = averaged
        duration_count = int(summary["duration_count"])
        duration_total = float(summary.get("duration_total") or 0.0)
        duration_min = cast(float | None, summary.get("duration_min"))
        duration_max = cast(float | None, summary.get("duration_max"))
        duration_last = cast(float | None, summary.get("duration_last"))
        duration_info = {
            "count": duration_count,
            "average": duration_total / duration_count if duration_count else None,
            "min": duration_min,
            "max": duration_max,
            "last": duration_last,
        }
        aggregated[stage_name] = {
            "count": count,
            "occurrences": summary["occurrences"],
            "status_counts": summary["status_counts"],
            "duration": duration_info,
            "totals": totals,
            "averages": averages,
            "last_metrics": summary["last_metrics"],
        }
    return aggregated


def aggregate_stage_metrics(
    entries: Iterable[dict[str, Any]], stage_filter: str | None = None
) -> dict[str, Any]:
    """Calculate average numeric metrics for each stage across history entries."""

    entries_list = list(entries)
    normalized_filter = _normalize_stage(stage_filter) or None
    summaries = _collect_stage_summaries(entries_list, normalized_filter)
    aggregated = _summaries_to_aggregated(summaries)

    return {
        "entries_processed": len(entries_list),
        "stage_filter": normalized_filter,
        "stage_metrics": aggregated,
        "run_ids": [
            entry.get("run_id") for entry in entries_list if entry.get("run_id")
        ],
    }
