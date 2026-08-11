"""Destructive full-span write and verify.

Writes a deterministic, offset-derived pattern across the whole device and
reads it back. Because every block's content is a function of its own offset, a
counterfeit card that silently wraps writes into a smaller physical area fails
verification at the wrapped offsets, and the offset recorded in the block that
*was* returned reveals where the write actually landed.

This replaces a stub that returned canned numbers (100% coverage, 120 MB/s)
without touching the device.

Details that matter for correctness:

* The verify pass must not read from the page cache, or a fake card's writes
  would be served back from RAM and every card would pass. The cache is dropped
  with POSIX_FADV_DONTNEED and the device reopened before verifying.
* Buffered writes hide media errors until fsync, so an fsync failure is a test
  failure, not something to ignore.
* Writing past a fake card's real capacity usually fails with EIO or ENOSPC.
  That is itself a positive detection, not an error to abort on.
* A block is only called "wrapped" when it exactly matches the pattern written
  for another offset. A header that merely decodes to a plausible integer is
  not enough: a bad sector returning zeros decodes as offset 0.

The block pattern, the offset decoder and the cache-dropping flush live in
`tfqa.core.blockio`, because the endurance engine writes and verifies with the
identical format and a second copy would drift.
"""

from __future__ import annotations

import time
from typing import Any, Literal, TypedDict

from tfqa.core.blockio import (
    HEADER,
    FlushOutcome,
    Mismatch,
    ProgressFn,
    block_pattern,
    decode_offset,
    flush_and_drop_cache,
    verify_pass,
    write_pass,
)
from tfqa.core.errors import ArgumentError, RuntimeIOError
from tfqa.core.models import DeviceInfo

#: Re-exported so callers and tests can reach them from the engine they belong
#: to; the definitions live in `tfqa.core.blockio` because the endurance engine
#: needs the identical header format.
__all__ = [
    "HEADER",
    "Mismatch",
    "block_pattern",
    "decode_offset",
    "FlushOutcome",
    "flush_and_drop_cache",
    "run_full_capacity",
    "validate_options",
    "verify_pass",
    "write_pass",
]

DEFAULT_BLOCK_SIZE = 1024 * 1024


class FullCapacityResult(TypedDict):
    status: Literal["ok", "fail"]
    message: str
    coverage_percent: float
    duration_seconds: float
    throughput_mbps: float
    issues: list[str]
    #: Something that weakens the evidence rather than something the device got
    #: wrong. Warnings never fail the run; issues do.
    warnings: list[str]
    details: dict[str, object]


def validate_options(block_size: int, limit_bytes: int | None) -> None:
    """Reject option values the engine cannot run.

    Split out so the CLI can apply the same rules before emitting a dry-run
    plan, and so a bad argument is reported as INVALID_ARGUMENT rather than
    falling through to a device-shaped error about capacity.
    """

    if block_size < HEADER.size:
        raise ArgumentError(
            message=(
                f"Block size must be at least {HEADER.size} bytes, the size of "
                "the offset header each block carries"
            ),
            details={"block_size": block_size, "minimum": HEADER.size},
        )
    if limit_bytes is not None and limit_bytes <= 0:
        raise ArgumentError(
            message="Limit must be a positive number of bytes",
            details={"limit_bytes": limit_bytes},
        )


def _estimate_real_size(written: int, span: int, wrapped: bool) -> int | None:
    """Real capacity, but only when it can actually be justified.

    A device that starts refusing writes has told us where its storage ends, so
    `written` is a sound estimate. A device that *wraps* has not: every offset
    reads back as some other offset's data, and recovering the period needs the
    binary search that `f3probe` performs (via `tfqa quick-test`). Returning the
    lowest mismatching offset looked like an answer but was typically 0.
    """

    if written < span:
        return written
    if wrapped:
        return None
    return None


def run_full_capacity(
    device: DeviceInfo,
    *,
    force: bool,
    yes: bool,
    block_size: int = DEFAULT_BLOCK_SIZE,
    limit_bytes: int | None = None,
    seed: int = 0,
    max_mismatches: int = 16,
    progress: ProgressFn | None = None,
) -> FullCapacityResult:
    """Write a pattern across the device and verify it reads back intact."""

    validate_options(block_size, limit_bytes)

    span = device.size_bytes
    if limit_bytes is not None:
        span = min(span, limit_bytes)
    if span <= 0:
        raise RuntimeIOError(
            "Device reports no capacity to test",
            {"device_path": device.path, "size_bytes": device.size_bytes},
        )

    started = time.monotonic()
    written, write_issues, warnings = write_pass(
        device.path, span, block_size, seed, progress=progress
    )
    verified, mismatches, _count, _wrapped, read_issues = verify_pass(
        device.path, written, block_size, seed, max_mismatches, progress=progress
    )
    duration = time.monotonic() - started

    issues = write_issues + read_issues
    coverage = (verified / span * 100) if span else 0.0
    # Both passes move `written` bytes, so throughput reflects the round trip.
    throughput = (
        (written + verified) / duration / (1024 * 1024) if duration > 0 else 0.0
    )

    wrapped = any("found_offset" in entry for entry in mismatches)
    real_size = _estimate_real_size(written, span, wrapped)
    if mismatches:
        issues.append(
            f"{len(mismatches)} block(s) failed verification"
            + (" (device appears to wrap writes)" if wrapped else "")
        )

    status: Literal["ok", "fail"] = "ok" if not issues and verified == span else "fail"
    if status == "ok":
        message = (
            f"Full capacity test passed: {verified} bytes written and verified "
            f"on {device.path}."
        )
    elif wrapped:
        message = (
            f"Fake capacity detected on {device.path}: writes wrap before the "
            f"reported {span} bytes."
        )
    else:
        message = f"Full capacity test failed for {device.path}."

    details: dict[str, Any] = {
        "device_path": device.path,
        "force_override": force,
        "confirmation": yes,
        "reported_size_bytes": device.size_bytes,
        "tested_span_bytes": span,
        "bytes_written": written,
        "bytes_verified": verified,
        "block_size": block_size,
        "seed": seed,
        "mismatches": mismatches,
        "wrapped": wrapped,
    }
    if real_size is not None:
        details["estimated_real_size_bytes"] = real_size
    elif wrapped:
        details["real_size_hint"] = (
            "Writes wrap, so the real capacity cannot be derived from this "
            "pass; run `tfqa quick-test`, which uses f3probe's binary search."
        )

    return FullCapacityResult(
        status=status,
        message=message,
        coverage_percent=round(coverage, 2),
        duration_seconds=round(duration, 3),
        throughput_mbps=round(throughput, 2),
        issues=issues,
        warnings=warnings,
        details=details,
    )
