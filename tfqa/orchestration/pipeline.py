"""Pipeline orchestration helpers for FlashCrucible."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import logging
from typing import Any, Callable, Iterable, Literal, cast

from tfqa.core.errors import ArgumentError
from tfqa.core.logging import emit_event
from tfqa.core.models import (
    DeviceInfo,
    EnduranceConfig,
    RunContext,
    TestResult,
    TestStatus,
)
from tfqa.orchestration.profile import EnduranceProfile
from tfqa.ext.fsck import run_fsck
from tfqa.ext.image import run_image_flash
from tfqa.tests.capacity import quick as quick_capacity
from tfqa.tests.capacity import full as full_capacity
from tfqa.tests.endurance import simple as endurance_simple
from tfqa.tests.health import snapshot as health_snapshot
from tfqa.tests.health.snapshot import HealthSnapshot
from tfqa.tests.performance import basic as perf_basic
from tfqa.tests.surface import scan as surface_scan
from tfqa.tests.workload import smallfiles as workload_smallfiles
from tfqa.reporting import summary as summary_mod
from tfqa.orchestration import checks as checks_mod

PipelineAction = Callable[[RunContext], dict[str, Any]]

_VALID_STATUSES = {
    "ok",
    "warning",
    "failed",
    "skipped",
    "error",
}

_LOGGER = logging.getLogger(__name__)

DEFAULT_STAGE_ORDER = [
    "detect",
    "quick-test",
    "full-capacity-test",
    "surface-scan",
    "filesystem-check",
    "performance",
    "workload-smallfiles",
    "endurance",
    "health",
    "summary",
]

# Stages that write raw blocks to the device, and so must clear the
# mounted/system-disk safety checks before a pipeline runs them.
#
# Deliberately absent:
#   surface-scan     `_surface_stage` calls run_surface_scan() with the default
#                    mode="readonly", which never writes. Only the standalone
#                    `tfqa surface-scan --mode destructive` writes, and that
#                    command guards itself.
#   filesystem-check `_filesystem_check_stage` calls run_fsck() with the default
#                    read_only=True.
#   workload-smallfiles  writes through a mounted filesystem, so demanding an
#                    unmounted device would make it unrunnable.
#   detect / health / summary  read only.
DESTRUCTIVE_STAGES = frozenset(
    {
        "quick-test",
        "full-capacity-test",
        "performance",
        "endurance",
        "image",
        "image-flash",
    }
)


def plan_is_destructive(stage_names: Iterable[str]) -> bool:
    """Return True when any stage in the plan writes to the device.

    Accepts both bare stage names (`quick-test`) and the prefixed form used in
    results (`pipeline.quick-test`).
    """

    return any(
        name.split(".")[-1].strip().lower() in DESTRUCTIVE_STAGES
        for name in stage_names
    )


def normalize_status(raw_status: Any | None) -> TestStatus:
    if raw_status is None:
        return "ok"
    if isinstance(raw_status, str):
        candidate = raw_status.lower()
    else:
        candidate = str(raw_status).lower()
    if candidate in _VALID_STATUSES:
        return cast(TestStatus, candidate)
    return "ok"


@dataclass(frozen=True)
class PipelineStage:
    """Describes a stage within a pipeline."""

    name: str
    action: PipelineAction


@dataclass(frozen=True)
class ImageFlashConfig:
    image_path: str
    block_size: str = "4M"
    conv_flags: tuple[str, ...] = ("fsync",)
    verify: bool = True
    write_timeout: float = 600.0
    verify_timeout: float = 300.0
    pre_quick_test: bool = False
    post_quick_test: bool = False
    pre_health: bool = False
    post_health: bool = False


def _image_flash_stage(config: ImageFlashConfig) -> PipelineStage:
    def action(ctx: RunContext) -> dict[str, Any]:
        payload = run_image_flash(
            config.image_path,
            ctx.device.path,
            block_size=config.block_size,
            conv_flags=config.conv_flags,
            write_timeout=config.write_timeout,
            verify_timeout=config.verify_timeout,
            verify=config.verify,
        )
        check_details: dict[str, dict[str, object]] = {}
        pre_records = _run_image_checks(
            ctx.run_id,
            ctx.device,
            ctx.log_dir,
            quick=config.pre_quick_test,
            health=config.pre_health,
            label="pre",
        )
        if pre_records:
            check_details["pre"] = pre_records
        post_records = _run_image_checks(
            ctx.run_id,
            ctx.device,
            ctx.log_dir,
            quick=config.post_quick_test,
            health=config.post_health,
            label="post",
        )
        if post_records:
            check_details["post"] = post_records
        details = {
            **cast(dict[str, Any], payload.get("details") or {}),
        }
        if check_details:
            details["checks"] = check_details
        details["image_path"] = config.image_path
        details["verify_enabled"] = config.verify
        return {
            "status": payload.get("status", "ok"),
            "metrics": payload.get("metrics", {}),
            "details": details,
        }

    return PipelineStage("image-flash", action)


def _run_image_checks(
    run_id: str,
    device: DeviceInfo,
    log_dir: Path | None,
    *,
    quick: bool,
    health: bool,
    label: Literal["pre", "post"],
) -> dict[str, object]:
    records: dict[str, object] = {}
    if quick:
        quick_result = checks_mod.quick_test_check(
            run_id,
            device,
            log_dir,
            label=label,
            free_space_only=False,
        )
        records["quick_test"] = quick_result.payload
    if health:
        health_result = checks_mod.health_snapshot_check(
            run_id,
            device,
            log_dir,
            label=label,
        )
        records["health_snapshot"] = health_result.payload
    return records


def _detect_stage(ctx: RunContext) -> dict[str, Any]:
    return {
        "status": "ok",
        "details": {"device": ctx.device.model_dump()},
    }


def _quick_stage(ctx: RunContext) -> dict[str, Any]:
    return quick_capacity.run_quick_capacity(ctx.device, free_space_only=False)


def _endurance_stage(profile: EnduranceProfile) -> PipelineStage:
    def action(ctx: RunContext) -> dict[str, Any]:
        config = EnduranceConfig(
            duration_seconds=profile.duration_seconds,
            pass_count=profile.pass_count,
            force=profile.force,
            write_pattern=profile.write_pattern,
        )
        result = endurance_simple.run_simple_endurance(ctx, config)
        return result.model_dump()

    return PipelineStage("endurance", action)


def _full_stage(ctx: RunContext) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        full_capacity.run_full_capacity(ctx.device, force=False, yes=False),
    )


def _surface_stage(ctx: RunContext) -> dict[str, Any]:
    return cast(dict[str, Any], surface_scan.run_surface_scan(ctx.device))


def _filesystem_check_stage(ctx: RunContext) -> dict[str, Any]:
    result = run_fsck(ctx.device.path)
    return {
        "status": result.status,
        "metrics": {
            "fsck_returncode": result.returncode,
            "fsck_clean": int(result.clean),
            "fsck_errors_fixed": int(result.errors_fixed),
            "fsck_needs_reboot": int(result.needs_reboot),
            "fsck_duration_seconds": result.duration_seconds,
        },
        "details": {"fsck": result.model_dump()},
    }


def _performance_stage(ctx: RunContext) -> dict[str, Any]:
    return cast(dict[str, Any], perf_basic.run_seq_performance(ctx.device))


def _workload_stage(ctx: RunContext) -> dict[str, Any]:
    result = workload_smallfiles.run_small_file_workload(
        ctx,
        workload_smallfiles.SmallFileWorkloadConfig(),
    )
    return result.model_dump()


def _health_stage(ctx: RunContext) -> dict[str, Any]:
    snapshot = health_snapshot.run_health_snapshot(ctx.device)
    metrics = {
        k: v
        for k, v in snapshot.get("health", {}).items()
        if isinstance(v, (int, float))
    }
    return {"status": "ok", "metrics": metrics, "details": snapshot}


def _collect_stage_health(ctx: RunContext) -> HealthSnapshot | None:
    try:
        return health_snapshot.run_health_snapshot(ctx.device)
    except Exception as exc:  # pragma: no cover
        _LOGGER.debug(
            "Failed to capture health snapshot during stage run for %s: %s",
            ctx.device.path,
            exc,
        )
        return None


def _summary_stage(ctx: RunContext) -> dict[str, Any]:
    summary = summary_mod.summarize_run(ctx.run_id, log_dir=ctx.log_dir)
    return {
        "status": summary.get("overall_status", "ok"),
        "metrics": {
            "event_count": summary.get("event_count", 0),
            "stage_count": len(summary.get("stage_summaries", [])),
        },
        "details": summary,
    }


def _normalize_stage_name(stage_name: str) -> str:
    candidate = stage_name.split(".")[-1].strip().lower()
    return candidate


def _build_stage(
    stage_name: str,
    profile: EnduranceProfile,
    image_config: ImageFlashConfig | None,
) -> PipelineStage:
    normalized = _normalize_stage_name(stage_name)
    if not normalized:
        raise ArgumentError(
            message="Pipeline stage name cannot be empty",
            details={"stage": stage_name},
        )
    if normalized == "detect":
        return PipelineStage("detect", _detect_stage)
    if normalized == "quick-test":
        return PipelineStage("quick-test", _quick_stage)
    if normalized == "full-capacity-test":
        return PipelineStage("full-capacity-test", _full_stage)
    if normalized == "surface-scan":
        return PipelineStage("surface-scan", _surface_stage)
    if normalized == "filesystem-check":
        return PipelineStage("filesystem-check", _filesystem_check_stage)
    if normalized in {"image", "image-flash"}:
        if image_config is None:
            raise ArgumentError(
                message="Pipeline stage 'image-flash' requires an image_path",
                details={"stage": stage_name},
            )
        return _image_flash_stage(image_config)
    if normalized == "performance":
        return PipelineStage("performance", _performance_stage)
    if normalized == "workload-smallfiles":
        return PipelineStage("workload-smallfiles", _workload_stage)
    if normalized == "endurance":
        return _endurance_stage(profile)
    if normalized == "health":
        return PipelineStage("health", _health_stage)
    if normalized == "summary":
        return PipelineStage("summary", _summary_stage)

    raise ArgumentError(
        message=f"Unknown pipeline stage: {stage_name}",
        details={"stage": stage_name},
    )


def build_pipeline(
    profile: EnduranceProfile,
    stage_names: Iterable[str] | None = None,
    image_config: ImageFlashConfig | None = None,
) -> list[PipelineStage]:
    order = list(stage_names) if stage_names is not None else DEFAULT_STAGE_ORDER
    stages: list[PipelineStage] = []
    for name in order:
        if not name or not name.strip():
            continue
        stages.append(_build_stage(name, profile, image_config))
    if not stages:
        raise ArgumentError(
            message="Pipeline plan must include at least one stage",
            details={"plan": order},
        )
    return stages


def build_default_pipeline(profile: EnduranceProfile) -> list[PipelineStage]:
    return build_pipeline(profile)


def run_pipeline(ctx: RunContext, stages: Iterable[PipelineStage]) -> list[TestResult]:
    results: list[TestResult] = []
    for stage in stages:
        started_at = datetime.now(timezone.utc)
        raw = stage.action(ctx)
        finished_at = datetime.now(timezone.utc)
        status = normalize_status(raw.get("status"))
        metrics = cast_metrics(raw)

        details: dict[str, Any] = {"stage": stage.name}
        details.update(raw.get("details", {}))
        details["raw"] = raw

        health_data = _collect_stage_health(ctx)
        if health_data:
            details["health_snapshot"] = health_data
            health_metrics = {
                k: v
                for k, v in health_data.get("health", {}).items()
                if isinstance(v, (int, float))
            }
            metrics = {**metrics, **health_metrics}

        log_path = emit_event(
            ctx.run_id,
            {
                "phase": "pipeline",
                "stage": stage.name,
                "status": status,
                "metrics": metrics,
            },
            log_dir=ctx.log_dir,
        )

        result = TestResult(
            name=f"pipeline.{stage.name}",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=(finished_at - started_at).total_seconds(),
            metrics=metrics,
            details=details,
            logs_path=log_path,
        )
        results.append(result)
    return results


def cast_metrics(raw: dict[str, Any]) -> dict[str, float | int]:
    metrics_value = raw.get("metrics")
    if isinstance(metrics_value, dict):
        metrics_dict = cast(dict[str, Any], metrics_value)
        filtered: dict[str, float | int] = {}
        for key, value in metrics_dict.items():
            if isinstance(value, (int, float)):
                filtered[key] = value
        return filtered

    fallback: dict[str, float | int] = {}
    for key, value in raw.items():
        if key == "status":
            continue
        if isinstance(value, (int, float)):
            fallback[key] = value
    return fallback
