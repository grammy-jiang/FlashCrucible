"""Deterministic block patterns and cache-defeating I/O.

Every engine that writes raw blocks and reads them back needs the same three
things, and each is subtle in a way that punishes a second copy:

* the block content has to be a function of its own offset, or a card that
  silently wraps writes into a smaller physical area passes verification;
* the offset a returned block *claims* is only evidence of a wrap when the
  whole block matches what was written for that offset, because a bad sector
  returning zeros decodes as offset ``0``;
* the verify pass has to read the card rather than the page cache, or a
  counterfeit's writes are served back from RAM and everything passes.

These lived in ``tfqa.tests.capacity.full``. They are here so the endurance
engine can use them rather than copy them: the header format must match
byte-for-byte between whatever writes and whatever verifies, and a drift there
is a verify pass that silently stops verifying.
"""

from __future__ import annotations

import hashlib
import os
import random as random_mod
import struct
from collections.abc import Iterator
from typing import Callable, Literal, NamedTuple, TypedDict, cast

from tfqa.core.errors import ArgumentError

#: (bytes done in this pass, bytes in a pass, phase name). The phase lets a
#: caller account for write and verify separately, and for each pass of a
#: multi-pass run separately again.
ProgressFn = Callable[[int, int, str], None]

#: Orders a pass can visit blocks in. Random order is a real difference to a
#: flash controller; because a block's content derives from its own offset, it
#: changes nothing about what verification expects.
WriteOrder = Literal["sequential", "random"]

#: offset, seed. Every block carries both, so a mismatch can say where the data
#: that came back actually belongs.
HEADER = struct.Struct("<QQ")

_DIGEST_SIZE = 32


def block_pattern(offset: int, size: int, seed: int) -> bytes:
    """Return the deterministic content a block at `offset` must hold.

    The offset is encoded in the header so a mismatch can say where the data
    that came back actually belongs, which is what exposes a wrapping card.
    """

    if size < HEADER.size:
        raise ArgumentError(
            message=(
                f"Block size must be at least {HEADER.size} bytes to carry the "
                "offset header"
            ),
            details={"size": size, "minimum": HEADER.size},
        )
    header = HEADER.pack(offset, seed)
    body = hashlib.blake2b(header, digest_size=_DIGEST_SIZE).digest()
    filler = body * ((size - len(header)) // _DIGEST_SIZE + 1)
    return header + filler[: size - len(header)]


def decode_offset(block: bytes, span: int | None = None) -> int | None:
    """Recover the offset a block claims to hold, if it looks like one.

    Corrupt data decodes to arbitrary integers -- all-0xFF bytes yield
    2**64-1 -- so a value outside the tested span is rejected rather than
    reported as though the device had returned a real block from elsewhere.
    """

    if len(block) < HEADER.size:
        return None
    try:
        offset, _seed = HEADER.unpack(block[: HEADER.size])
    except struct.error:  # pragma: no cover - guard
        return None
    offset = int(offset)
    if span is not None and offset >= span:
        return None
    return offset


class FlushOutcome(NamedTuple):
    """What happened when the writes were committed and the cache dropped.

    Two separate problems, kept separate. A failed `fsync` means the data never
    reached the card. A failed cache drop means the data may have reached it but
    the verify pass could be answered from RAM -- the write is fine, the
    *evidence* is not.
    """

    sync_error: str | None = None
    cache_error: str | None = None


def flush_and_drop_cache(fd: int) -> FlushOutcome:
    """Commit the writes and ask the kernel to forget the pages.

    The flush is where a device reports the media errors that buffered writes
    hid, so swallowing it would let dirty pages satisfy the verify reads and the
    test pass even though nothing reached the card.

    Dropping the cache matters for the same reason: without it the verify pass
    reads back from RAM and a counterfeit passes cleanly. A failure to drop it
    is therefore reported, not discarded.

    A clean result is **not** a guarantee that the cache is cold.
    `POSIX_FADV_DONTNEED` is advisory: the kernel may decline to evict pages and
    still return success, and on some filesystems the call does nothing at all.
    It is the strongest request available, not a promise -- which is why the
    verify pass also reopens the device rather than relying on this alone.
    """

    sync_error: str | None = None
    try:
        os.fsync(fd)
    except OSError as exc:
        sync_error = f"fsync failed: {exc.strerror or exc}"

    fadvise = getattr(os, "posix_fadvise", None)
    if fadvise is None:  # pragma: no cover - platform guard
        return FlushOutcome(sync_error, "posix_fadvise is unavailable on this platform")
    try:
        fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError as exc:
        return FlushOutcome(
            sync_error, f"could not drop the page cache: {exc.strerror or exc}"
        )
    return FlushOutcome(sync_error, None)


class Mismatch(TypedDict, total=False):
    """One block that did not read back as it was written."""

    offset: int
    expected_offset: int
    found_offset: int
    reason: str


class WriteOutcome(NamedTuple):
    written: int
    issues: list[str]
    warnings: list[str]


class VerifyOutcome(NamedTuple):
    verified: int
    #: Described mismatches, capped. The cap is on the *detail*, not the count.
    mismatches: list[Mismatch]
    #: Every mismatch, including those past the cap. An endurance run needs the
    #: true count per pass, because the trend is the measurement.
    mismatch_count: int
    wrapped: bool
    issues: list[str]


def block_offsets(
    span: int,
    block_size: int,
    order: WriteOrder = "sequential",
    seed: int = 0,
) -> Iterator[int]:
    """The offsets a pass visits, in the order it visits them.

    A final chunk too small to carry the offset header is dropped: it could not
    be verified, so writing it would put data on the device that nothing can
    check.

    Sequential order is streamed rather than listed. A span of 64 GiB at the
    smallest permitted block size is 4.3 billion offsets, and materialising
    them would exhaust memory before the device was opened.

    Random order has to hold them all -- a shuffle cannot be lazy -- so it
    carries that cost by nature, and a caller asking for it on a huge span with
    a tiny block is choosing it.
    """

    if order == "random":
        offsets = [
            offset
            for offset in range(0, span, block_size)
            if min(block_size, span - offset) >= HEADER.size
        ]
        random_mod.Random(seed).shuffle(offsets)
        yield from offsets
        return

    for offset in range(0, span, block_size):
        if min(block_size, span - offset) < HEADER.size:
            return
        yield offset


def write_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    *,
    order: WriteOrder = "sequential",
    phase: str = "write",
    progress: ProgressFn | None = None,
) -> WriteOutcome:
    """Write the pattern across `span`, and commit it.

    Shared by every engine that writes raw blocks. The alternative -- a copy per
    engine -- means the format one writes can drift from the format another
    verifies, and a verify pass that silently stops verifying is the failure
    mode with no symptom.
    """

    issues: list[str] = []
    warnings: list[str] = []
    written = 0
    # Sequential writes advance the file position by themselves; seeking before
    # each one would be a syscall per block for nothing.
    seeking = order != "sequential"

    fd = os.open(path, os.O_WRONLY)
    try:
        for offset in block_offsets(span, block_size, order, seed):
            size = min(block_size, span - offset)
            try:
                if seeking:
                    os.lseek(fd, offset, os.SEEK_SET)
                os.write(fd, block_pattern(offset, size, seed))
            except OSError as exc:
                # A fake card typically starts refusing writes at its real size.
                issues.append(
                    f"write failed at offset {offset} after {written} bytes: "
                    f"{exc.strerror or exc}"
                )
                break
            written += size
            if progress:
                progress(written, span, phase)

        flushed = flush_and_drop_cache(fd)
        if flushed.sync_error:
            issues.append(
                f"{flushed.sync_error} after {written} bytes; the device did "
                "not commit the data it accepted"
            )
        elif flushed.cache_error:
            # Only when the write is believed to have committed. If fsync
            # failed the run has already failed for a stronger reason, and
            # doubting the evidence for data that never arrived is noise.
            warnings.append(
                f"{flushed.cache_error}; the verify pass may have been served "
                "from the page cache, so a device that silently wraps its "
                "writes could still appear to pass"
            )
    finally:
        os.close(fd)
    return WriteOutcome(written, issues, warnings)


def verify_pass(
    path: str,
    span: int,
    block_size: int,
    seed: int,
    max_mismatches: int,
    *,
    phase: str = "verify",
    progress: ProgressFn | None = None,
) -> VerifyOutcome:
    """Read `span` back and compare it against what was written.

    The wrap test lives here and nowhere else. It is deliberately strict: a bad
    sector returning zeros decodes as offset 0, so a header that merely holds a
    plausible integer is not evidence. Only a block that is byte-for-byte what
    was written for the offset it claims counts as a wrap.
    """

    issues: list[str] = []
    mismatches: list[Mismatch] = []
    mismatch_count = 0
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
                mismatch_count += 1
                found = decode_offset(actual, span)
                is_wrap = (
                    found is not None
                    and found != offset
                    and actual == block_pattern(found, size, seed)
                )
                wrapped = wrapped or is_wrap
                # The cap limits how much is described, never what is counted.
                if len(mismatches) < max_mismatches:
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

            offset += size
            if progress:
                progress(offset, span, phase)
    finally:
        os.close(fd)
    return VerifyOutcome(verified, mismatches, mismatch_count, wrapped, issues)
