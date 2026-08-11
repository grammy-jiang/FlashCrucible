#!/usr/bin/env python3
"""Small example: parse `tests/fixtures/sample-run.jsonl` into a simple RunSummary.

This script is intentionally minimal and demonstrates how JSONL events are structured
and how to aggregate basic metrics (duration, total written, errors).
"""

import json
from datetime import datetime
from pathlib import Path


def parse_jsonl(path: Path):
    events = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def summarize(events):
    if not events:
        return {}
    run_id = events[0].get("run_id")
    device = events[0].get("device", {}).get("path")
    start = datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(events[-1]["timestamp"].replace("Z", "+00:00"))
    duration = (end - start).total_seconds()
    throughput = None
    total_written_mb = 0
    total_errors = 0
    for e in events:
        m = e.get("metrics", {})
        total_written_mb += m.get("written_mb", 0)
        total_errors += m.get("errors", 0)
        if "throughput_mbps" in m:
            throughput = m["throughput_mbps"]

    return {
        "run_id": run_id,
        "device": device,
        "duration_seconds": duration,
        "total_written_mb": total_written_mb,
        "total_errors": total_errors,
        "throughput_mbps": throughput,
    }


def main():
    path = (
        Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample-run.jsonl"
    )
    events = parse_jsonl(path)
    summary = summarize(events)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
