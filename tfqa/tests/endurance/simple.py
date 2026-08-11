"""Endurance/burn-in engine for FlashCrucible.

Not implemented. The previous version performed no device I/O at all: it
computed throughput from `is_removable`, derived bytes written from that, and
generated an error count as `pass_index // 2`. Run against a device path that
did not exist it returned "58 TB written, 0 errors" in under a millisecond, and
those figures went into the run history where `trends` aggregates them.

Rather than keep inventing them, the engine refuses. Implementing it for real
means writing to the device across many passes, which makes it a genuinely
long-running command -- see the tracking issues on the repository.
"""

from __future__ import annotations


from tfqa.core.errors import ArgumentError, NotImplementedEngineError
from tfqa.core.models import (
    EnduranceConfig,
    RunContext,
    TestResult,
)


def validate_config(config: EnduranceConfig) -> None:
    """Reject a config the engine cannot run.

    Split out from the engine so the CLI can apply the same rules before
    emitting a dry-run plan; otherwise `--dry-run` advertises a plan that the
    real invocation would refuse.
    """

    if config.duration_seconds <= 0:
        raise ArgumentError(
            message="Endurance duration must be positive",
            details={"duration_seconds": config.duration_seconds},
        )

    if config.pass_count <= 0:
        raise ArgumentError(
            message="Pass count must be at least 1",
            details={"pass_count": config.pass_count},
        )


def run_simple_endurance(ctx: RunContext, config: EnduranceConfig) -> TestResult:
    """Refuse to run: this engine cannot measure anything yet.

    Arguments are still validated first, so a caller learns about a bad
    `--duration` or `--passes` rather than being told only that the engine is
    missing.
    """

    validate_config(config)

    raise NotImplementedEngineError(
        "endurance",
        "the engine performs no device I/O, so any metric it reported would be "
        "invented; use quick-test or full-capacity-test for real measurements",
        {
            "device_path": ctx.device.path,
            "requested_passes": config.pass_count,
            "requested_duration_seconds": config.duration_seconds,
        },
    )
