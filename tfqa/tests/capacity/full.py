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
"""

from __future__ import annotations

import hashlib
import os
import struct
import time
from typing import Any, Callable, Literal, TypedDict, cast

from tfqa.core.errors import ArgumentError, RuntimeIOError
from tfqa.core.models import DeviceInfo

DEFAULT_BLOCK_SIZE = 1024 * 1024
_HEADER = struct.Struct("<QQ")  # offset, seed
_DIGEST_SIZE = 32

#: (bytes done in this pass, bytes in a pass, pass name). The pass name lets a
#: caller account for write and verify separately; reporting both as one span
#: showed 100% when writing finished and then dropped back to nearly zero.
ProgressFn = Callable[[int, int, str], None]


class Mismatch(TypedDict, total=False):
    offset: int
    expected_offset: int
    found_offset: int
    reason: str


class FullCapacityResult(TypedDict):
    status: Literal["ok", "fail"]
    message: str
    coverage_percent: float
    duration_seconds: float
    throughput_mbps: float
    issues: list[str]
    details: dict[str, object]


def block_pattern(offset: int, size: int, seed: int) -> bytes:
    """Return the deterministic content a block at `offset` must hold.

    The offset is encoded in the header so a mismatch can say where the data
    that came back actually belongs, which is what exposes a wrapping card.
    """

    if size < _HEADER.size:
        raise ArgumentError(
            message=(
                f"Block size must be at least {_HEADER.size} bytes to carry the "
                "offset header"
            ),
            details={"size": size, "minimum": _HEADER.size},
        )
    header = _HEADER.pack(offset, seed)
    body = hashlib.blake2b(header, digest_size=_DIGEST_SIZE).digest()
    filler = body * ((size - len(header)) // _DIGEST_SIZE + 1)
    return header + filler[: size - len(header)]


def _decode_offset(block: bytes, span: int | None = None) -> int | None:
    """Recover the offset a block claims to hold, if it looks like one.

    Corrupt data decodes to arbitrary integers -- all-0xFF bytes yield
    2**64-1 -- so a value outside the tested span is rejected rather than
    reported as though the device had returned a real block from elsewhere.
    """

    if len(block) < _HEADER.size:
        return None
    try:
        offset, _seed = _HEADER.unpack(block[: _HEADER.size])
    except struct.error:  # pragma: no cover - guard
        return None
    offset = int(offset)
    if span is not None and offset >= span:
        return None
    return offset


def _flush_and_drop_cache(fd: int) -> str | None:
    """Commit the writes and ask the kernel to forget the pages.

    Returns a description of an fsync failure, or None. The flush is where a
    device reports the media errors that buffered writes hid, so swallowing it
    would let dirty pages satisfy the verify reads and the test pass even though
    nothing reached the card.

    Dropping the cache matters for the same reason: without it the verify pass
    reads back from RAM and a counterfeit passes cleanly.
    """

    error: str | None = None
    try:
        os.fsync(fd)
    except OSError as exc:
        error = f"fsync failed: {exc.strerror or exc}"
    fadvise = getattr(os, "posix_fadvise", None)
    if fadvise is None:  # pragma: no cover - platform guard
        return error
    try:
        fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:  # pragma: no cover - advisory only
        pass
    return error


def _write_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    progress: ProgressFn | None,
) -> tuple[int, list[str]]:
    """Fill `span` bytes with the pattern. Returns (bytes written, issues)."""

    issues: list[str] = []
    written = 0
    fd = os.open(path, os.O_WRONLY)
    try:
        while written < span:
            size = min(block_size, span - written)
            if size < _HEADER.size:
                # A tail too small to carry the offset header cannot be
                # verified; stop rather than write something uncheckable.
                break
            try:
                chunk = block_pattern(written, size, seed)
                os.write(fd, chunk)
            except OSError as exc:
                # A fake card typically starts refusing writes at its real size.
                issues.append(
                    f"write failed at offset {written} after "
                    f"{written} bytes: {exc.strerror or exc}"
                )
                break
            written += size
            if progress:
                progress(written, span, "write")
        flush_error = _flush_and_drop_cache(fd)
        if flush_error:
            issues.append(
                f"{flush_error} after {written} bytes; the device did not "
                "commit the data it accepted"
            )
    finally:
        os.close(fd)
    return written, issues


def _verify_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    max_mismatches: int,
    progress: ProgressFn | None,
) -> tuple[int, list[Mismatch], list[str]]:
    """Read the span back and compare. Returns (verified, mismatches, issues)."""

    issues: list[str] = []
    mismatches: list[Mismatch] = []
    verified = 0
    fd = os.open(path, os.O_RDONLY)
    try:
        offset = 0
        while offset < span:
            size = min(block_size, span - offset)
            if size < _HEADER.size:
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
            if actual != block_pattern(offset, size, seed):
                if len(mismatches) < max_mismatches:
                    found = _decode_offset(actual, span)
                    # A plausible integer in the header is not enough: a bad
                    # sector returning zeros decodes as offset 0. Only call it a
                    # wrap when the whole block is exactly what was written for
                    # that other offset, otherwise it is ordinary corruption.
                    is_wrap = (
                        found is not None
                        and found != offset
                        and actual == block_pattern(found, size, seed)
                    )
                    entry = Mismatch(offset=offset, expected_offset=offset)
                    if is_wrap:
                        entry["found_offset"] = cast(int, found)
                        entry["reason"] = (
                            "block holds data written for a different offset, "
                            "which is how a wrapping counterfeit behaves"
                        )
                    else:
                        entry["reason"] = "block contents differ from the pattern"
                    mismatches.append(entry)
            else:
                verified += size
            offset += size
            if progress:
                progress(offset, span, "verify")
    finally:
        os.close(fd)
    return verified, mismatches, issues


def validate_options(block_size: int, limit_bytes: int | None) -> None:
    """Reject option values the engine cannot run.

    Split out so the CLI can apply the same rules before emitting a dry-run
    plan, and so a bad argument is reported as INVALID_ARGUMENT rather than
    falling through to a device-shaped error about capacity.
    """

    if block_size < _HEADER.size:
        raise ArgumentError(
            message=(
                f"Block size must be at least {_HEADER.size} bytes, the size of "
                "the offset header each block carries"
            ),
            details={"block_size": block_size, "minimum": _HEADER.size},
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
    written, write_issues = _write_pass(device.path, span, block_size, seed, progress)
    verified, mismatches, read_issues = _verify_pass(
        device.path, written, block_size, seed, max_mismatches, progress
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
        details=details,
    )
