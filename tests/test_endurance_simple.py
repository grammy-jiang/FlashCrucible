"""The endurance engine measures what it reports, and stops when it should.

The previous version performed no device I/O. It computed throughput from
`is_removable`, derived bytes written from that, and generated an error count as
`pass_index // 2`. Against a device path that did not exist it returned "58 TB
written, 0 errors" in under a millisecond, and those figures went into the run
history where `trends` aggregates them.

So these tests do not only check that numbers come back. They check that the
numbers come from the device: every pass runs against a real file standing in
for a block device, and the cases that matter are the ones where the device
misbehaves.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from unittest.mock import patch

import pytest

from tfqa.core.errors import ArgumentError
from tfqa.core import blockio
from tfqa.core.models import DeviceInfo, EnduranceConfig, RunContext, TestResult
from tfqa.tests.endurance import simple
from tfqa.tests.endurance.simple import run_simple_endurance

SPAN = 64 * 1024
BLOCK = 8 * 1024

NO_WEAR = {
    "source": "none",
    "available": False,
    "cid": {},
    "health": {},
    "sources": {},
    "details": {},
}


def _make_device(path: str, size: int = SPAN) -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=Path(path).name,
        model="TestDevice",
        vendor="Vendor",
        serial="SN",
        size_bytes=size,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def _make_context(device: DeviceInfo, log_dir: Path) -> RunContext:
    return RunContext(
        run_id="run-1",
        started_at=datetime.now(timezone.utc),
        device=device,
        mode="human",
        log_dir=log_dir,
    )


def _target(root: Path, size: int = SPAN) -> Path:
    image = root / "device.img"
    image.write_bytes(b"\0" * size)
    return image


def _run(root: Path, **overrides: object) -> TestResult:
    image = _target(root)
    device = _make_device(str(image), image.stat().st_size)
    settings: dict[str, object] = {
        "duration_seconds": 60.0,
        "pass_count": 2,
        "block_size": BLOCK,
    }
    settings.update(overrides)
    config = EnduranceConfig(**settings)  # type: ignore[arg-type]
    with patch.object(simple, "run_health_snapshot", return_value=NO_WEAR):
        return run_simple_endurance(_make_context(device, root), config)


class TestItActuallyWrites:
    def test_every_pass_writes_and_verifies_the_span(self, tmp_path: Path) -> None:
        result = _run(tmp_path)

        assert result.status == "ok"
        assert result.metrics["passes_completed"] == 2
        # Counted as they were written, not derived from anything.
        assert result.metrics["bytes_written"] == SPAN * 2
        assert result.metrics["bytes_verified"] == SPAN * 2

    def test_the_device_holds_the_last_pass_pattern(self, tmp_path: Path) -> None:
        # Proof the bytes reached the file rather than a counter.
        image = _target(tmp_path)
        device = _make_device(str(image))
        with patch.object(simple, "run_health_snapshot", return_value=NO_WEAR):
            run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=2, block_size=BLOCK),
            )

        from tfqa.core.blockio import block_pattern

        last_seed = simple._pass_seed(0, 1)
        assert image.read_bytes()[:BLOCK] == block_pattern(0, BLOCK, last_seed)

    def test_each_pass_writes_a_different_pattern(self) -> None:
        # Rewriting identical bytes lets a controller skip the work, so every
        # pass would measure less than the one before it.
        assert simple._pass_seed(0, 0) != simple._pass_seed(0, 1)

    def test_a_limit_bounds_the_span(self, tmp_path: Path) -> None:
        result = _run(tmp_path, limit_bytes=BLOCK * 2)
        assert result.metrics["span_bytes"] == BLOCK * 2
        assert result.metrics["bytes_written"] == BLOCK * 2 * 2


class TestItMeasuresRatherThanEstimates:
    def test_throughput_is_zero_when_nothing_was_timed(self) -> None:
        # Better than a plausible number for a measurement never taken.
        assert simple._throughput_mbps(1024, 0) == 0.0
        assert simple._throughput_mbps(0, 1.0) == 0.0

    def test_retention_is_a_ratio_of_two_measurements(self, tmp_path: Path) -> None:
        result = _run(tmp_path)
        # Present only because both passes were timed; never a projection.
        assert "write_throughput_retention" in result.metrics

    def test_no_lifetime_estimate_is_reported(self, tmp_path: Path) -> None:
        # The numbers the old engine invented. Reporting them again from a
        # handful of passes would be the same lie in a new costume.
        result = _run(tmp_path)
        forbidden = {"lifetime", "tbw", "health_score", "life_remaining", "wear_score"}
        for key in result.metrics:
            assert not any(word in key.lower() for word in forbidden), key

    def test_absent_wear_data_says_so_instead_of_reporting_zero(
        self, tmp_path: Path
    ) -> None:
        # "Unchanged" and "never readable" are different answers.
        result = _run(tmp_path)
        wear = result.details["wear"]
        assert wear["available"] is False
        assert "root" in wear["reason"] or "sdmon" in wear["reason"]
        assert any("No wear data" in warning for warning in result.warnings)

    def test_wear_deltas_come_from_the_card(self, tmp_path: Path) -> None:
        before = {**NO_WEAR, "available": True, "health": {"life_used_percent": 10}}
        after = {**NO_WEAR, "available": True, "health": {"life_used_percent": 12}}
        image = _target(tmp_path)
        device = _make_device(str(image))
        with patch.object(simple, "run_health_snapshot", side_effect=[before, after]):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=1, block_size=BLOCK),
            )

        fields = result.details["wear"]["fields"]
        assert fields["life_used_percent"] == {"before": 10, "after": 12, "delta": 2}


class TestWearProvenance:
    """A delta between two different sources is an artefact, not a measurement."""

    def test_a_source_change_makes_the_delta_unavailable(self, tmp_path: Path) -> None:
        # sdmon reads a vendor register; EXT_CSD reports a 10% band. Subtracting
        # one from the other manufactures wear out of a tool appearing or
        # disappearing mid-run.
        before = {
            **NO_WEAR,
            "available": True,
            "source": "mmc-utils",
            "health": {"life_used_percent": 20},
        }
        after = {
            **NO_WEAR,
            "available": True,
            "source": "sdmon",
            "health": {"life_used_percent": 30},
        }
        image = _target(tmp_path)
        device = _make_device(str(image))
        with patch.object(simple, "run_health_snapshot", side_effect=[before, after]):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=1, block_size=BLOCK),
            )

        wear = result.details["wear"]
        assert wear["available"] is False
        assert "source changed" in wear["reason"]
        # The fictitious +10 must not appear anywhere.
        assert "fields" not in wear


class TestSpanIsWhatWasTested:
    def test_a_tail_too_small_to_verify_is_excluded(self, tmp_path: Path) -> None:
        # A remainder shorter than the offset header cannot be verified, so the
        # passes skipped it while the result still counted it as tested.
        odd = BLOCK * 3 + 4
        image = tmp_path / "odd.img"
        image.write_bytes(b"\0" * odd)
        device = _make_device(str(image), odd)
        with patch.object(simple, "run_health_snapshot", return_value=NO_WEAR):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=1, block_size=BLOCK),
            )

        assert result.metrics["span_bytes"] == BLOCK * 3
        assert result.metrics["bytes_written"] == BLOCK * 3
        assert result.metrics["bytes_verified"] == BLOCK * 3

    def test_a_verifiable_tail_is_kept(self, tmp_path: Path) -> None:
        odd = BLOCK * 3 + 64
        image = tmp_path / "odd2.img"
        image.write_bytes(b"\0" * odd)
        device = _make_device(str(image), odd)
        with patch.object(simple, "run_health_snapshot", return_value=NO_WEAR):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=1, block_size=BLOCK),
            )

        assert result.metrics["span_bytes"] == odd
        assert result.metrics["bytes_written"] == odd


class TestStopping:
    def test_the_pass_count_is_a_limit(self, tmp_path: Path) -> None:
        result = _run(tmp_path, pass_count=3)
        assert result.metrics["passes_completed"] == 3
        assert result.details["stopped_because"] == "pass count reached"

    def test_the_deadline_stops_it_between_passes(self, tmp_path: Path) -> None:
        # Checked between passes, never mid-span: half a pass verifies nothing.
        # The clock is driven rather than raced, so the test cannot depend on
        # how fast a 64 KB write happens to be on the machine running it.
        result = self._run_with_a_driven_clock(tmp_path)

        assert result.metrics["passes_completed"] == 1
        assert result.details["stopped_because"] == "the time limit was reached"

    def test_a_partial_pass_is_never_recorded(self, tmp_path: Path) -> None:
        result = self._run_with_a_driven_clock(tmp_path)
        for entry in result.details["passes"]:
            assert entry["bytes_written"] == SPAN

    @staticmethod
    def _run_with_a_driven_clock(root: Path) -> TestResult:
        ticks = count(start=0.0, step=1.0)
        image = _target(root)
        device = _make_device(str(image))
        with (
            patch.object(simple, "run_health_snapshot", return_value=NO_WEAR),
            patch.object(simple.time, "monotonic", side_effect=lambda: next(ticks)),
        ):
            return run_simple_endurance(
                _make_context(device, root),
                EnduranceConfig(duration_seconds=0.5, pass_count=5, block_size=BLOCK),
            )

    def test_a_device_refusing_writes_stops_the_run(self, tmp_path: Path) -> None:
        # A card that has stopped accepting data has answered the question.
        image = _target(tmp_path)
        device = _make_device(str(image))
        real_write = os.write
        calls = {"n": 0}

        def failing(fd: int, data: bytes) -> int:
            calls["n"] += 1
            if calls["n"] > 2:
                raise OSError(28, "No space left on device")
            return real_write(fd, data)

        with (
            patch.object(simple, "run_health_snapshot", return_value=NO_WEAR),
            patch("os.write", side_effect=failing),
        ):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=5, block_size=BLOCK),
            )

        assert result.status == "failed"
        assert (
            result.details["stopped_because"] == "the device stopped accepting writes"
        )
        assert result.metrics["passes_completed"] == 1

    def test_a_wrap_stops_the_run(self, tmp_path: Path) -> None:
        # Wear is worth measuring over many passes; a counterfeit is not.
        image = _target(tmp_path, SPAN)
        device = _make_device(str(image), SPAN)
        with (
            patch.object(simple, "run_health_snapshot", return_value=NO_WEAR),
            patch.object(
                simple, "verify_pass", return_value=(0, [], 1, True, ["wrap"])
            ),
        ):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=5, block_size=BLOCK),
            )

        assert result.status == "failed"
        assert "counterfeit" in result.details["stopped_because"]
        assert result.metrics["passes_completed"] == 1

    def test_an_ordinary_mismatch_does_not_stop_the_run(self, tmp_path: Path) -> None:
        # Watching the count grow across passes is the measurement; stopping at
        # the first bad block throws that away.
        image = _target(tmp_path)
        device = _make_device(str(image))
        with (
            patch.object(simple, "run_health_snapshot", return_value=NO_WEAR),
            patch.object(
                simple, "verify_pass", return_value=(0, [], 3, False, ["bad"])
            ),
        ):
            result = run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=3, block_size=BLOCK),
            )

        assert result.metrics["passes_completed"] == 3
        assert result.metrics["mismatches"] == 9
        assert result.status == "failed"


class TestWritePatterns:
    """A pattern the engine only echoes back is a claim about a run that did
    not happen -- and random and sequential stress a card differently."""

    def test_random_visits_every_block_exactly_once(self) -> None:
        offsets = list(blockio.block_offsets(SPAN, BLOCK, "random", 7))
        assert sorted(offsets) == list(range(0, SPAN, BLOCK))

    def test_random_is_not_sequential(self) -> None:
        assert list(blockio.block_offsets(SPAN, BLOCK, "random", 7)) != list(
            range(0, SPAN, BLOCK)
        )

    def test_random_is_reproducible_from_the_seed(self) -> None:
        assert list(blockio.block_offsets(SPAN, BLOCK, "random", 7)) == list(
            blockio.block_offsets(SPAN, BLOCK, "random", 7)
        )

    def test_a_random_pass_still_verifies(self, tmp_path: Path) -> None:
        # Order does not change what verification expects, because a block's
        # content is derived from its own offset.
        result = _run(tmp_path, write_pattern="random")
        assert result.status == "ok"
        assert result.metrics["bytes_verified"] == SPAN * 2

    def test_sequential_order_is_streamed_not_materialised(self) -> None:
        # A 64 GiB span at the smallest permitted block size is 4.3 billion
        # offsets; listing them would exhaust memory before the device was
        # opened. Taking three from a huge span proves nothing was built.
        from itertools import islice

        assert list(islice(blockio.block_offsets(2**36, 16), 3)) == [0, 16, 32]

    def test_an_unsupported_pattern_is_refused(self, tmp_path: Path) -> None:
        device = _make_device(str(_target(tmp_path)))
        with pytest.raises(ArgumentError) as excinfo:
            run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(write_pattern="zigzag"),
            )
        assert "zigzag" in str(excinfo.value.details)

    def test_every_shipped_profile_names_a_pattern_the_engine_runs(self) -> None:
        # `router-telemetry` asks for "random"; before this the engine wrote
        # sequentially and reported "random" anyway.
        from tfqa.core.models import ConfigModel
        from tfqa.orchestration import profile as profile_mod

        for entry in profile_mod.list_profiles(ConfigModel()):
            if entry.get("error"):
                continue
            assert entry["write_pattern"] in simple.WRITE_PATTERNS, entry["name"]


class TestValidation:
    def test_inputs_are_checked_before_anything_is_written(
        self, tmp_path: Path
    ) -> None:
        device = _make_device(str(_target(tmp_path)))
        ctx = _make_context(device, tmp_path)

        with pytest.raises(ArgumentError):
            run_simple_endurance(ctx, EnduranceConfig(duration_seconds=0.0))
        with pytest.raises(ArgumentError):
            run_simple_endurance(ctx, EnduranceConfig(pass_count=0))
        with pytest.raises(ArgumentError):
            run_simple_endurance(ctx, EnduranceConfig(block_size=4))
        with pytest.raises(ArgumentError):
            run_simple_endurance(ctx, EnduranceConfig(limit_bytes=0))
        # The profile loader already refuses this, so the engine must too --
        # otherwise whether a value is legal depends on where it came from.
        with pytest.raises(ArgumentError):
            run_simple_endurance(ctx, EnduranceConfig(max_mismatches=0))

    def test_a_device_too_small_to_verify_is_refused(self, tmp_path: Path) -> None:
        # A block too small to carry the offset header cannot be checked, so
        # writing it would produce data nothing can verify.
        image = tmp_path / "tiny.img"
        image.write_bytes(b"\0")
        device = _make_device(str(image), 1)
        with pytest.raises(ArgumentError):
            run_simple_endurance(
                _make_context(device, tmp_path), EnduranceConfig(block_size=BLOCK)
            )


class TestProgress:
    def test_both_phases_are_reported_for_every_pass(self, tmp_path: Path) -> None:
        image = _target(tmp_path)
        device = _make_device(str(image))
        seen: list[tuple[int, int, str]] = []
        with patch.object(simple, "run_health_snapshot", return_value=NO_WEAR):
            run_simple_endurance(
                _make_context(device, tmp_path),
                EnduranceConfig(duration_seconds=60.0, pass_count=2, block_size=BLOCK),
                progress=lambda done, total, phase: seen.append((done, total, phase)),
            )

        # Each pass names its own phases. Reusing "write"/"verify" made the
        # recorder key every pass to the same slot, so progress fell back at
        # the start of each pass and could never exceed 1/pass_count.
        assert {phase for _d, _t, phase in seen} == {
            "pass0-write",
            "pass0-verify",
            "pass1-write",
            "pass1-verify",
        }
        assert len(seen) == 2 * 2 * (SPAN // BLOCK)

    def test_progress_reaches_the_end_across_every_pass(self, tmp_path: Path) -> None:
        # The bug this pins: two passes finished at 50%.
        from tfqa.cli.main import _progress_recorder
        from tfqa.core import runstate

        status = runstate.RunStatus(run_id="r", command="endurance")
        record = _progress_recorder(status, tmp_path, passes=2 * 2)
        for index in range(2):
            record(SPAN, SPAN, f"pass{index}-write")
            record(SPAN, SPAN, f"pass{index}-verify")

        assert status.percent == 100.0
        assert status.wrote_to_device
