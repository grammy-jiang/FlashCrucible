from __future__ import annotations

import unittest

from tfqa.core.errors import ArgumentError
from tfqa.orchestration.pipeline import (
    DEFAULT_STAGE_ORDER,
    build_pipeline,
)
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
