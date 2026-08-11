"""No engine may report a metric it did not measure.

Four engines used to synthesise results when they could not do real work, all
of them writing the invented figures into `metrics`, which is what
`tfqa.reporting.trends` aggregates. Where a marker existed it sat in `details`,
which `trends` never reads:

| engine              | invented                          | marked            |
| ------------------- | --------------------------------- | ----------------- |
| performance/basic   | throughput                        | details.mode      |
| performance/random  | throughput                        | details.mode      |
| surface/scan        | coverage_percent, read_errors     | details.tool      |
| endurance/simple    | bytes written, errors, throughput | nothing at all    |

These tests pin the replacement contract for all of them at once, so a new
engine cannot quietly reintroduce the pattern.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tfqa.core.errors import (
    NotImplementedEngineError,
    RuntimeIOError,
    TFQAError,
    TimeoutError,
    ToolNotFoundError,
)
from tfqa.core.models import DeviceInfo, EnduranceConfig, RunContext
from tfqa.orchestration import pipeline as pipeline_mod
from tfqa.orchestration.profile import EnduranceProfile
from tfqa.tests.endurance import simple as endurance_simple
from tfqa.tests.performance import basic as perf_basic
from tfqa.tests.performance import random as perf_random
from tfqa.tests.surface import scan as surface_scan

DEVICE = DeviceInfo(
    path="/dev/sdz",
    name="sdz",
    model="Model",
    vendor="Vendor",
    serial="SN",
    size_bytes=64 * 1024**3,
    is_removable=True,
    is_system_disk=False,
    mountpoints=[],
    transport="usb",
)

PROFILE = EnduranceProfile(
    name="default",
    duration_seconds=1.0,
    pass_count=2,
    force=False,
    write_pattern="sequential",
)


def _context() -> RunContext:
    return RunContext(
        run_id="run-1",
        started_at=datetime.now(timezone.utc),
        device=DEVICE,
        config_profile="default",
        destructive=False,
        mode="ai",
        log_dir=Path("/tmp"),
    )


class TestEnginesRefuseWhenTheyCannotMeasure:
    def test_sequential_performance_needs_fio(self) -> None:
        with (
            patch("tfqa.ext.fio.run_fio_job", side_effect=ToolNotFoundError("fio")),
            pytest.raises(ToolNotFoundError),
        ):
            perf_basic.run_seq_performance(DEVICE)

    def test_random_performance_needs_fio(self) -> None:
        with (
            patch("tfqa.ext.fio.run_fio_job", side_effect=ToolNotFoundError("fio")),
            pytest.raises(ToolNotFoundError),
        ):
            perf_random.run_random_performance(DEVICE)

    def test_surface_scan_needs_badblocks(self) -> None:
        with (
            patch(
                "tfqa.ext.badblocks.run_badblocks_readonly",
                side_effect=ToolNotFoundError("badblocks"),
            ),
            pytest.raises(ToolNotFoundError),
        ):
            surface_scan.run_surface_scan(DEVICE)

    def test_endurance_is_not_implemented(self) -> None:
        # No tool is missing here; the engine simply does no I/O.
        with pytest.raises(NotImplementedEngineError):
            endurance_simple.run_simple_endurance(
                _context(), EnduranceConfig(duration_seconds=1.0, pass_count=1)
            )


class TestNoSyntheticFallbacksRemain:
    """A structural guard, so the pattern cannot creep back in unnoticed.

    Checked with the AST rather than by searching the source text: the modules
    legitimately *describe* the old behaviour in comments, and a word search
    would trip over the explanation of the very thing it is policing.
    """

    ENGINE_MODULES = (perf_basic, perf_random, surface_scan)

    @staticmethod
    def _tool_handlers(module: object) -> list[ast.ExceptHandler]:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = []
            if isinstance(node.type, ast.Name):
                names = [node.type.id]
            elif isinstance(node.type, ast.Tuple):
                names = [e.id for e in node.type.elts if isinstance(e, ast.Name)]
            if "ToolNotFoundError" in names:
                found.append(node)
        return found

    @pytest.mark.parametrize("module", ENGINE_MODULES, ids=lambda m: m.__name__)
    def test_a_missing_tool_is_always_re_raised(self, module: object) -> None:
        # Not catching at all is equally compliant -- the error propagates and
        # no fallback can exist -- so this only checks the handlers that are
        # there. The runtime tests above assert the errors actually propagate.
        for handler in self._tool_handlers(module):
            body = [n for n in handler.body if not isinstance(n, ast.Pass)]
            assert len(body) == 1 and isinstance(body[0], ast.Raise), (
                f"{module.__name__} handles ToolNotFoundError with something other "  # type: ignore[attr-defined]
                "than a bare re-raise; synthesising a result is not allowed"
            )
            assert body[0].exc is None, "re-raise the original error unchanged"

    def test_endurance_performs_no_device_io(self) -> None:
        # If someone implements it for real, replace this with a test that
        # checks the I/O actually happens.
        source = inspect.getsource(endurance_simple)
        for primitive in ("os.write(", "os.open(", "subprocess."):
            assert primitive not in source


class TestPipelineRecordsSkippedNotOk:
    """A stage that cannot run must not be recorded as a pass."""

    def _run(self, stage_name: str, target: str, error: TFQAError):
        stage = pipeline_mod.build_pipeline(PROFILE, [stage_name])[0]
        with patch(target, side_effect=error):
            (result,) = pipeline_mod.run_pipeline(_context(), [stage])
        return result

    def test_performance_stage_is_skipped(self) -> None:
        result = self._run(
            "performance", "tfqa.ext.fio.run_fio_job", ToolNotFoundError("fio")
        )
        assert result.status == "skipped"
        assert result.details["error_code"] == "EXT_TOOL_MISSING"
        assert result.metrics == {}

    def test_surface_stage_is_skipped(self) -> None:
        result = self._run(
            "surface-scan",
            "tfqa.ext.badblocks.run_badblocks_readonly",
            ToolNotFoundError("badblocks"),
        )
        assert result.status == "skipped"
        assert result.metrics == {}

    def test_endurance_stage_is_skipped(self) -> None:
        stage = pipeline_mod.build_pipeline(PROFILE, ["endurance"])[0]
        (result,) = pipeline_mod.run_pipeline(_context(), [stage])

        assert result.status == "skipped"
        assert result.details["error_code"] == "NOT_IMPLEMENTED"
        assert result.metrics == {}

    def test_a_real_failure_is_not_disguised_as_skipped(self) -> None:
        # Only a capability gap becomes "skipped". A tool that ran and failed
        # is a real error and must not let the pipeline quietly continue.
        stage = pipeline_mod.build_pipeline(PROFILE, ["performance"])[0]
        with patch(
            "tfqa.ext.fio.run_fio_job", side_effect=RuntimeIOError("fio blew up", {})
        ):
            with pytest.raises(RuntimeIOError):
                pipeline_mod.run_pipeline(_context(), [stage])

    def test_a_timeout_is_not_disguised_as_skipped(self) -> None:
        stage = pipeline_mod.build_pipeline(PROFILE, ["surface-scan"])[0]
        with patch(
            "tfqa.ext.badblocks.run_badblocks_readonly",
            side_effect=TimeoutError("badblocks timed out", 1.0, {}),
        ):
            with pytest.raises(TimeoutError):
                pipeline_mod.run_pipeline(_context(), [stage])

    def test_a_skipped_stage_contributes_no_metrics_to_trends(self) -> None:
        # The whole point: an unavailable engine must not put a number into the
        # history that `trends` would then average.
        stage = pipeline_mod.build_pipeline(PROFILE, ["endurance"])[0]
        (result,) = pipeline_mod.run_pipeline(_context(), [stage])

        assert not result.metrics
