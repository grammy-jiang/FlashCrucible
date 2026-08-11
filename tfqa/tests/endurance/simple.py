"""Endurance / burn-in: repeated write-and-verify passes over the device.

The question this answers is "how long will this card last", which no single
pass can. A card that writes and verifies perfectly once may slow down, start
returning bad blocks, or stop accepting writes after a few full rewrites, and
that trajectory is the result -- not any one pass.

So every pass writes the span and reads it back, and the numbers that matter
are the ones that change between passes: throughput per pass, mismatches per
pass, and where the writes stopped being accepted.

What it will not do
-------------------

The previous version performed no device I/O at all. It computed throughput
from `is_removable`, derived bytes written from that, and generated an error
count as `pass_index // 2`. Run against a device path that did not exist it
returned "58 TB written, 0 errors" in under a millisecond, and those figures
went into the run history where `trends` aggregates them.

Nothing here is estimated. Bytes are counted as they are written, durations are
measured with a monotonic clock, mismatches are counted from comparisons, and
wear comes from the card's own registers or is reported as unavailable with the
reason. There is deliberately no lifetime estimate, no TBW-remaining figure and
no health score: those are precisely the numbers that were invented before.

Stop conditions
---------------

`pass_count` and `duration_seconds` are both limits, and the run stops at
whichever is reached first, recording which one it was. A pass in progress is
allowed to finish rather than being cut mid-span, because half a pass verifies
nothing.

Two things stop it early:

* the device refusing writes -- a card that has stopped accepting data has
  answered the question;
* a detected wrap -- the block came back holding data written for a different
  offset, which is a counterfeit rather than wear, and continuing to hammer it
  learns nothing.

A read mismatch that is not a wrap does *not* stop the run. Watching the count
grow across passes is the measurement.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypedDict, cast

from tfqa.core.blockio import HEADER, block_pattern, decode_offset, flush_and_drop_cache
from tfqa.core.errors import ArgumentError
from tfqa.core.models import EnduranceConfig, RunContext, TestResult, TestStatus
from tfqa.tests.health.snapshot import HealthSnapshot, run_health_snapshot

#: (bytes done in this pass, bytes in a pass, phase). Phases are named
#: "write"/"verify" so a caller can account for the two halves separately.
ProgressFn = Callable[[int, int, str], None]

DEFAULT_BLOCK_SIZE = 1024 * 1024

#: Beyond a handful, more mismatches from one pass say nothing new; the count
#: still rises so the trend survives.
DEFAULT_MAX_MISMATCHES = 8

#: Wear fields worth reporting a delta for. Anything absent from the card's
#: registers is simply absent from the result.
_WEAR_KEYS = (
    "life_used_percent",
    "life_time_est_typ_a",
    "life_time_est_typ_b",
    "pre_eol_info",
    "power_on_count",
    "read_error_count",
    "write_error_count",
    "spare_block_count",
)


class PassResult(TypedDict):
    """What one write-and-verify cycle measured."""

    index: int
    bytes_written: int
    bytes_verified: int
    mismatches: int
    wrapped: bool
    duration_seconds: float
    write_throughput_mbps: float
    verify_throughput_mbps: float
    issues: list[str]


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

    if config.block_size < HEADER.size:
        raise ArgumentError(
            message=(
                f"Block size must be at least {HEADER.size} bytes, the size of "
                "the offset header every block carries"
            ),
            details={"block_size": config.block_size, "minimum": HEADER.size},
        )

    if config.limit_bytes is not None and config.limit_bytes <= 0:
        raise ArgumentError(
            message="Limit must be a positive number of bytes",
            details={"limit_bytes": config.limit_bytes},
        )


def _throughput_mbps(byte_count: int, seconds: float) -> float:
    """Measured, not estimated: zero elapsed time means no measurement."""

    if seconds <= 0 or byte_count <= 0:
        return 0.0
    return round(byte_count / seconds / (1024 * 1024), 2)


def _pass_seed(base_seed: int, index: int) -> int:
    """A distinct pattern per pass.

    Rewriting identical bytes lets a controller skip the work -- deduplicating,
    or noticing the block is unchanged -- so every pass would measure less than
    the one before it.
    """

    return base_seed + index * 0x9E3779B1


def _write_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    progress: ProgressFn | None,
) -> tuple[int, list[str], list[str]]:
    """Fill `span` bytes. Returns (bytes written, issues, warnings)."""

    issues: list[str] = []
    warnings: list[str] = []
    written = 0
    fd = os.open(path, os.O_WRONLY)
    try:
        while written < span:
            size = min(block_size, span - written)
            if size < HEADER.size:
                break
            try:
                os.write(fd, block_pattern(written, size, seed))
            except OSError as exc:
                issues.append(
                    f"write failed at offset {written}: {exc.strerror or exc}"
                )
                break
            written += size
            if progress:
                progress(written, span, "write")
        flushed = flush_and_drop_cache(fd)
        if flushed.sync_error:
            issues.append(
                f"{flushed.sync_error} after {written} bytes; the device did "
                "not commit the data it accepted"
            )
        elif flushed.cache_error:
            warnings.append(
                f"{flushed.cache_error}; the verify pass may have been served "
                "from the page cache, so a device that silently wraps its "
                "writes could still appear to pass"
            )
    finally:
        os.close(fd)
    return written, issues, warnings


def _verify_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    max_mismatches: int,
    progress: ProgressFn | None,
) -> tuple[int, int, bool, list[str]]:
    """Read `span` back and compare.

    Returns (bytes verified, mismatch count, wrapped, issues). The mismatch
    count keeps rising past `max_mismatches`; only the recorded detail stops,
    because the count across passes is the measurement.
    """

    issues: list[str] = []
    mismatches = 0
    wrapped = False
    verified = 0
    fd = os.open(path, os.O_RDONLY)
    try:
        offset = 0
        while offset < span:
            size = min(block_size, span - offset)
            if size < HEADER.size:
                break
            try:
                actual = os.read(fd, size)
            except OSError as exc:
                issues.append(f"read failed at offset {offset}: {exc.strerror or exc}")
                break
            if len(actual) < size:
                issues.append(
                    f"short read at offset {offset}: got {len(actual)} of {size} bytes"
                )
                break
            if actual == block_pattern(offset, size, seed):
                verified += size
            else:
                mismatches += 1
                found = decode_offset(actual, span)
                # A plausible integer in the header is not enough: a bad sector
                # returning zeros decodes as offset 0. Only a block that is
                # exactly what was written for another offset is a wrap.
                if (
                    found is not None
                    and found != offset
                    and actual == block_pattern(found, size, seed)
                ):
                    wrapped = True
                    if mismatches <= max_mismatches:
                        issues.append(
                            f"block at offset {offset} holds data written for "
                            f"offset {found}, which is how a wrapping "
                            "counterfeit behaves"
                        )
                elif mismatches <= max_mismatches:
                    issues.append(f"block at offset {offset} differs from the pattern")
            offset += size
            if progress:
                progress(offset, span, "verify")
    finally:
        os.close(fd)
    return verified, mismatches, wrapped, issues


def _wear_delta(before: HealthSnapshot, after: HealthSnapshot) -> dict[str, object]:
    """What the card's own counters say changed, if it says anything.

    Absent registers produce an absent delta rather than a zero: "unchanged"
    and "never readable" are different answers, and reporting the second as the
    first is how an unmeasured number gets treated as a measurement.
    """

    if not before.get("available") or not after.get("available"):
        return {
            "available": False,
            "reason": (
                "no wear source answered; eMMC EXT_CSD registers usually need "
                "root, and sdmon only supports some industrial SD cards"
            ),
            "sources": after.get("sources", {}),
        }

    deltas: dict[str, object] = {}
    for key in _WEAR_KEYS:
        start = before["health"].get(key)
        end = after["health"].get(key)
        if isinstance(start, int) and isinstance(end, int):
            deltas[key] = {"before": start, "after": end, "delta": end - start}
    return {"available": True, "source": after.get("source"), "fields": deltas}


def run_simple_endurance(  # noqa: C901 - the loop is the algorithm
    ctx: RunContext,
    config: EnduranceConfig,
    progress: ProgressFn | None = None,
) -> TestResult:
    """Write and verify the span repeatedly, and report what changed."""

    validate_config(config)

    device = ctx.device
    span = device.size_bytes
    if config.limit_bytes is not None:
        span = min(span, config.limit_bytes)
    if span < HEADER.size:
        raise ArgumentError(
            message="Device reports too little capacity to test",
            details={"device_path": device.path, "size_bytes": device.size_bytes},
        )

    started_at = ctx.started_at
    before = run_health_snapshot(device)

    passes: list[PassResult] = []
    warnings: list[str] = []
    stopped_because = "pass count reached"
    total_written = 0
    total_verified = 0
    total_mismatches = 0
    wrapped = False

    monotonic_start = time.monotonic()
    deadline = monotonic_start + config.duration_seconds

    for index in range(config.pass_count):
        seed = _pass_seed(config.seed, index)
        pass_started = time.monotonic()

        written, write_issues, write_warnings = _write_pass(
            device.path, span, config.block_size, seed, progress
        )
        write_finished = time.monotonic()
        verified, mismatches, pass_wrapped, read_issues = _verify_pass(
            device.path,
            written,
            config.block_size,
            seed,
            config.max_mismatches,
            progress,
        )
        finished = time.monotonic()

        warnings.extend(write_warnings)
        total_written += written
        total_verified += verified
        total_mismatches += mismatches
        wrapped = wrapped or pass_wrapped

        passes.append(
            PassResult(
                index=index,
                bytes_written=written,
                bytes_verified=verified,
                mismatches=mismatches,
                wrapped=pass_wrapped,
                duration_seconds=round(finished - pass_started, 3),
                write_throughput_mbps=_throughput_mbps(
                    written, write_finished - pass_started
                ),
                verify_throughput_mbps=_throughput_mbps(
                    verified, finished - write_finished
                ),
                issues=write_issues + read_issues,
            )
        )

        if write_issues:
            # A card that has stopped accepting writes has answered the
            # question; hammering it further measures nothing.
            stopped_because = "the device stopped accepting writes"
            break
        if pass_wrapped:
            stopped_because = "the device wraps its writes, so it is counterfeit"
            break
        if index + 1 < config.pass_count and time.monotonic() >= deadline:
            # Checked between passes, never during: a pass cut mid-span leaves
            # nothing coherent to verify.
            stopped_because = "the time limit was reached"
            break

    elapsed = time.monotonic() - monotonic_start
    after = run_health_snapshot(device)
    wear = _wear_delta(before, after)
    if not wear.get("available"):
        warnings.append(
            "No wear data: " + cast(str, wear.get("reason", "no source answered"))
        )

    completed = len(passes)
    failed = wrapped or total_mismatches > 0 or any(p["issues"] for p in passes)
    status: TestStatus = "failed" if failed else "ok"

    first = passes[0]["write_throughput_mbps"] if passes else 0.0
    last = passes[-1]["write_throughput_mbps"] if passes else 0.0

    metrics: dict[str, float] = {
        "passes_completed": float(completed),
        "passes_requested": float(config.pass_count),
        "bytes_written": float(total_written),
        "bytes_verified": float(total_verified),
        "mismatches": float(total_mismatches),
        "elapsed_seconds": round(elapsed, 3),
        "span_bytes": float(span),
    }
    if first > 0:
        # A ratio of two measurements, not a projection: 0.8 means the last
        # pass wrote at 80% of the first pass's rate.
        metrics["write_throughput_retention"] = round(last / first, 3)

    summary = (
        f"Ran {completed} of {config.pass_count} pass(es) over {span} bytes; "
        f"stopped because {stopped_because}."
    )

    return TestResult(
        name="endurance.simple",
        status=status,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        duration_seconds=round(elapsed, 3),
        metrics=metrics,
        details=cast(
            dict[str, Any],
            {
                "summary": summary,
                "device_path": device.path,
                "span_bytes": span,
                "block_size": config.block_size,
                "write_pattern": config.write_pattern,
                "stopped_because": stopped_because,
                "wrapped": wrapped,
                "passes": passes,
                "wear": wear,
            },
        ),
        warnings=warnings,
        logs_path=None,
    )
