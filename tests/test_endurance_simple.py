"""Unit tests for the endurance test harness."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tfqa.core.errors import ArgumentError, NotImplementedEngineError
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


def test_endurance_refuses_rather_than_inventing_metrics(tmp_path: Path) -> None:
    """The engine does no device I/O, so it must not report having done any.

    It used to compute throughput from `is_removable`, derive bytes written
    from that, and generate errors as `pass_index // 2`. Against a device path
    that did not exist it returned "58 TB written, 0 errors" in under a
    millisecond, with no marker anywhere to say it was synthetic.
    """

    device = _make_device("/dev/sdb")
    ctx = _make_context(device, "run-1", tmp_path)

    with pytest.raises(NotImplementedEngineError) as excinfo:
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=1.0, pass_count=3))

    assert excinfo.value.error_code == "NOT_IMPLEMENTED"
    assert excinfo.value.details["engine"] == "endurance"
    assert excinfo.value.details["device_path"] == "/dev/sdb"


def test_endurance_emits_no_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing happened, so nothing should reach the run history.
    device = _make_device("/dev/sdb")
    ctx = _make_context(device, "run-2", tmp_path)
    recorded: list[object] = []

    def spy(*args: object, **kwargs: object) -> Path:
        recorded.append(args)
        return tmp_path / "x.jsonl"

    monkeypatch.setattr("tfqa.tests.endurance.simple.emit_event", spy, raising=False)

    with pytest.raises(NotImplementedEngineError):
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=1.0, pass_count=3))

    assert recorded == []


def test_endurance_validates_inputs(tmp_path: Path) -> None:
    """Arguments are still checked first, ahead of the not-implemented refusal."""

    device = _make_device("/dev/sdd")
    ctx = _make_context(device, "run-3", tmp_path)

    with pytest.raises(ArgumentError):
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=0.0, pass_count=1))

    with pytest.raises(ArgumentError):
        run_simple_endurance(ctx, EnduranceConfig(duration_seconds=1.0, pass_count=0))
