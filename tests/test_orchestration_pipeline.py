from __future__ import annotations

import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from tfqa.core.errors import ArgumentError
from tfqa.core.models import DeviceInfo, EnduranceConfig, RunContext
from tfqa.orchestration.pipeline import (
    DEFAULT_STAGE_ORDER,
    PipelineStage,
    build_pipeline,
    normalize_status,
    run_pipeline,
)
from tfqa.orchestration import pipeline as pipeline_mod
from tfqa.orchestration.profile import EnduranceProfile


def _make_profile() -> EnduranceProfile:
    return EnduranceProfile(
        name="test-profile",
        duration_seconds=1.0,
        pass_count=1,
        force=False,
        write_pattern="sequential",
    )


class TestOrchestrationPipeline(unittest.TestCase):
    def test_build_pipeline_default_order(self) -> None:
        profile = _make_profile()
        stages = build_pipeline(profile)
        self.assertEqual(
            [stage.name for stage in stages],
            [f"{name}" for name in DEFAULT_STAGE_ORDER],
        )

    def test_build_pipeline_from_plan_preserves_order(self) -> None:
        profile = _make_profile()
        stages = build_pipeline(profile, ["quick-test", "detect"])
        self.assertEqual([stage.name for stage in stages], ["quick-test", "detect"])

    def test_build_pipeline_unknown_stage_raises(self) -> None:
        profile = _make_profile()
        with self.assertRaises(ArgumentError):
            build_pipeline(profile, ["quick-test", "mystery-stage"])

    def test_build_pipeline_rejects_empty_plan(self) -> None:
        profile = _make_profile()
        with self.assertRaises(ArgumentError):
            build_pipeline(profile, [])


class TestEnduranceIsDestructiveInAPlan(unittest.TestCase):
    """A plan of endurance plus read-only stages still overwrites the card.

    While the engine refused to do device I/O, excluding it kept a mounted card
    from answering DEVICE_UNSAFE before the caller learned the engine did not
    exist. Now that it writes, that exclusion would skip both the safety
    preview and `_assert_device_safe` before the writes.
    """

    def test_endurance_is_a_destructive_stage(self) -> None:
        self.assertIn("endurance", pipeline_mod.DESTRUCTIVE_STAGES)

    def test_a_plan_containing_it_is_destructive(self) -> None:
        self.assertTrue(pipeline_mod.plan_is_destructive(["health", "endurance"]))

    def test_the_prefixed_form_counts_too(self) -> None:
        self.assertTrue(pipeline_mod.plan_is_destructive(["pipeline.endurance"]))

    def test_a_read_only_plan_is_still_not_destructive(self) -> None:
        self.assertFalse(
            pipeline_mod.plan_is_destructive(["health", "filesystem-check"])
        )


class TestTheEnduranceStageHonoursTheProfile(unittest.TestCase):
    def test_max_mismatches_reaches_the_engine(self) -> None:
        # Dropped from the stage config, a profile's value was silently
        # replaced by the default, so the same profile behaved differently
        # under `pipeline` than under `endurance`.
        profile = EnduranceProfile(
            name="p", duration_seconds=1.0, pass_count=1, max_mismatches=3
        )
        captured: dict[str, EnduranceConfig] = {}

        def fake_run(ctx, config, progress=None):  # type: ignore[no-untyped-def]
            captured["config"] = config
            raise ArgumentError(message="stop here", details={})

        context = RunContext(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            device=DeviceInfo(
                path="/dev/sdz",
                name="sdz",
                size_bytes=1024,
                is_removable=True,
                is_system_disk=False,
                mountpoints=[],
            ),
        )
        stage = pipeline_mod.build_pipeline(profile, ["endurance"])[0]
        with patch(
            "tfqa.tests.endurance.simple.run_simple_endurance", side_effect=fake_run
        ):
            with self.assertRaises(ArgumentError):
                stage.action(context)

        self.assertEqual(captured["config"].max_mismatches, 3)


class TestNormalizeStatus(unittest.TestCase):
    """The engines say "fail"; the pipeline vocabulary says "failed".

    Unmapped values used to fall through to "ok", so a counterfeit detected by
    quick-test or full-capacity-test was recorded as a passing stage.
    """

    def test_engine_failure_is_not_reported_as_success(self) -> None:
        self.assertEqual(normalize_status("fail"), "failed")

    def test_synonyms_map_onto_the_vocabulary(self) -> None:
        for raw, expected in (
            ("fail", "failed"),
            ("failure", "failed"),
            ("FAIL", "failed"),
            (" fail ", "failed"),
            ("pass", "ok"),
            ("success", "ok"),
            ("warn", "warning"),
            ("skip", "skipped"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_status(raw), expected)

    def test_canonical_values_pass_through(self) -> None:
        for value in ("ok", "warning", "failed", "skipped", "error"):
            with self.subTest(value=value):
                self.assertEqual(normalize_status(value), value)

    def test_absent_status_counts_as_ok(self) -> None:
        self.assertEqual(normalize_status(None), "ok")

    def test_unknown_status_is_an_error_not_a_pass(self) -> None:
        # We do not know that it succeeded, so it must not read as success.
        self.assertEqual(normalize_status("banana"), "error")


class TestFailurePropagation(unittest.TestCase):
    def _context(self) -> RunContext:
        return RunContext(
            run_id="test-run",
            started_at=datetime.now(timezone.utc),
            device=DeviceInfo(
                path="/dev/sdz",
                name="sdz",
                size_bytes=1024,
                is_removable=True,
                is_system_disk=False,
                mountpoints=[],
                transport="usb",
            ),
            config_profile="default",
            destructive=False,
            mode="ai",
            log_dir=Path("/tmp"),
        )

    def test_a_failing_stage_is_recorded_as_failed(self) -> None:
        # A detected counterfeit must not surface as a passing stage.
        stage = PipelineStage(
            "full-capacity-test",
            lambda _ctx: {
                "status": "fail",
                "message": "Fake capacity detected",
                "metrics": {},
                "details": {},
            },
        )

        (result,) = run_pipeline(self._context(), [stage])

        self.assertEqual(result.status, "failed")
