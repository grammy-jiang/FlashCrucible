"""Unit tests for the endurance test harness."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tfqa.core.errors import ArgumentError
from tfqa.core.models import DeviceInfo, EnduranceConfig, RunContext
from tfqa.tests.endurance.simple import run_simple_endurance


def _make_device(path: str, removable: bool = True) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="TestDevice",
        vendor="Vendor",
        serial="SN",
        size_bytes=64 * 1024**3,
        is_removable=removable,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _make_context(device: DeviceInfo, run_id: str, log_dir: Path) -> RunContext:
    return RunContext(
        run_id=run_id,
        started_at=datetime.now(timezone.utc),
        device=device,
        mode="human",
        log_dir=log_dir,
    )


def test_endurance_aggregates_pass_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _make_device("/dev/sdb")
    ctx = _make_context(device, "run-1", tmp_path)
    config = EnduranceConfig(duration_seconds=1.0, pass_count=3, force=True)

    recorded: list[dict[str, object]] = []

    def fake_emit(
        run_id: str, event: dict[str, object], log_dir: Path | None = None
    ) -> Path:
        recorded.append(event)
        return tmp_path / "run-1.jsonl"

    monkeypatch.setattr("tfqa.tests.endurance.simple.emit_event", fake_emit)

    result = run_simple_endurance(ctx, config)

    assert result.status == "ok"
    assert len(result.details["pass_history"]) == 3
    assert result.metrics["total_errors"] == 0
    assert recorded[-1]["pass_index"] == 3
    assert result.logs_path == tmp_path / "run-1.jsonl"


def test_endurance_warns_when_errors_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = _make_device("/dev/sdc", removable=False)
    ctx = _make_context(device, "run-2", tmp_path)
    config = EnduranceConfig(duration_seconds=0.5, pass_count=2, force=False)

    def noop_emit(*_args: object, **_kwargs: object) -> Path:
        return tmp_path / "run-2.jsonl"

    monkeypatch.setattr("tfqa.tests.endurance.simple.emit_event", noop_emit)

    result = run_simple_endurance(ctx, config)

    assert result.status == "warning"
    assert result.metrics["total_errors"] > 0
    assert "observed" in result.warnings[0]


def test_endurance_validates_inputs(tmp_path: Path) -> None:
    device = _make_device("/dev/sdd")
    ctx = _make_context(device, "run-3", tmp_path)

    with pytest.raises(ArgumentError):
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=0.0, pass_count=1))

    with pytest.raises(ArgumentError):
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=1.0, pass_count=0))
