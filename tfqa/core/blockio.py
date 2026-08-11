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
import struct
from typing import NamedTuple

from tfqa.core.errors import ArgumentError

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
