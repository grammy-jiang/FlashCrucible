"""Tests for the destructive full-span write and verify engine.

`run_full_capacity` used to return canned numbers (100% coverage, 120 MB/s,
600s) without touching the device, so a counterfeit card passed cleanly. These
tests drive the real engine against a file standing in for a block device,
including a device that wraps its writes the way a fake-capacity card does.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from tfqa.core.errors import ArgumentError, RuntimeIOError
from tfqa.core.models import DeviceInfo
from tfqa.tests.capacity import full

SPAN = 64 * 1024
BLOCK = 8 * 1024


def make_device(path: Path, size_bytes: int = SPAN) -> DeviceInfo:
    return DeviceInfo(
        path=str(path),
        name=path.name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=size_bytes,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


def make_target(tmp_path: Path, size_bytes: int = SPAN) -> Path:
    target = tmp_path / "device.img"
    target.write_bytes(b"\x00" * size_bytes)
    return target


class TempDirCase(TestCase):
    """Each test gets its own directory, cleaned up afterwards."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class BlockPattern(TestCase):
    def test_is_deterministic(self):
        self.assertEqual(full.block_pattern(0, 512, 0), full.block_pattern(0, 512, 0))

    def test_differs_by_offset(self):
        self.assertNotEqual(
            full.block_pattern(0, 512, 0), full.block_pattern(512, 512, 0)
        )

    def test_differs_by_seed(self):
        self.assertNotEqual(
            full.block_pattern(0, 512, 0), full.block_pattern(0, 512, 1)
        )

    def test_has_the_requested_length(self):
        for size in (16, 512, 4096):
            with self.subTest(size=size):
                self.assertEqual(len(full.block_pattern(0, size, 0)), size)

    def test_encodes_its_own_offset(self):
        # This is what lets a mismatch say where the returned data belongs.
        block = full.block_pattern(4096, 512, 0)
        self.assertEqual(full.decode_offset(block), 4096)


class HealthyDevice(TempDirCase):
    def test_passes_and_reports_full_coverage(self):
        target = make_target(self.root)
        result = full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coverage_percent"], 100.0)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["details"]["bytes_written"], SPAN)
        self.assertEqual(result["details"]["bytes_verified"], SPAN)
        self.assertFalse(result["details"]["wrapped"])

    def test_actually_writes_the_pattern(self):
        target = make_target(self.root)
        full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )

        # The stub never touched the device; this must.
        self.assertEqual(target.read_bytes()[:BLOCK], full.block_pattern(0, BLOCK, 0))

    def test_measured_values_are_not_canned(self):
        target = make_target(self.root)
        result = full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )

        # The stub always answered 600.0s / 120.0 MB/s.
        self.assertNotEqual(result["duration_seconds"], 600.0)
        self.assertGreaterEqual(result["duration_seconds"], 0.0)
        self.assertGreater(result["throughput_mbps"], 0.0)


class CounterfeitDevice(TempDirCase):
    """A fake card that reports more capacity than it physically stores."""

    def _wrapping_target(self, root: Path, real_size: int) -> Path:
        target = root / "fake.img"
        target.write_bytes(b"\x00" * real_size)
        return target

    def test_wrapping_writes_are_detected(self):
        real_size = SPAN // 4
        target = self._wrapping_target(self.root, real_size)

        # Emulate a wrapping card: writes past the real size land back at the
        # start, exactly what a fake-capacity controller does.
        original_write = os.write
        state = {"pos": 0}

        def wrapping_write(fd: int, data: bytes) -> int:
            if state["pos"] >= real_size:
                os.lseek(fd, state["pos"] % real_size, os.SEEK_SET)
            written = original_write(fd, data)
            state["pos"] += written
            os.lseek(fd, state["pos"] % real_size, os.SEEK_SET)
            return written

        device = make_device(target, size_bytes=SPAN)
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(os, "write", wrapping_write)
            result = full.run_full_capacity(
                device, force=True, yes=True, block_size=BLOCK
            )

        self.assertEqual(result["status"], "fail")
        self.assertLess(result["coverage_percent"], 100.0)
        self.assertTrue(result["details"]["mismatches"])
        self.assertTrue(result["details"]["wrapped"])
        self.assertIn("Fake capacity detected", result["message"])
        # A wrapping device does not reveal its real size in this pass, so no
        # number is offered — only a pointer at the probe that can find it.
        self.assertNotIn("estimated_real_size_bytes", result["details"])
        self.assertIn("quick-test", result["details"]["real_size_hint"])

    def test_writes_refused_past_the_real_size_are_reported(self):
        # A fake card usually starts failing writes at its real capacity. The
        # engine records that and does not treat the span as covered.
        real_size = SPAN // 2
        target = self._wrapping_target(self.root, real_size)

        original_write = os.write
        state = {"pos": 0}

        def refusing_write(fd: int, data: bytes) -> int:
            if state["pos"] >= real_size:
                raise OSError(5, "Input/output error")
            written = original_write(fd, data)
            state["pos"] += written
            return written

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(os, "write", refusing_write)
            result = full.run_full_capacity(
                make_device(target, size_bytes=SPAN),
                force=True,
                yes=True,
                block_size=BLOCK,
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["details"]["bytes_written"], real_size)
        self.assertLess(result["details"]["bytes_verified"], SPAN)
        self.assertLess(result["coverage_percent"], 100.0)
        self.assertTrue(any("write failed" in issue for issue in result["issues"]))
        # Here the real size *is* known: the device stopped accepting writes.
        self.assertEqual(result["details"]["estimated_real_size_bytes"], real_size)

    def test_mismatch_records_where_the_data_belonged(self):
        target = make_target(self.root)
        full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )
        # Overwrite one block with the pattern for a different offset.
        with open(target, "r+b") as handle:
            handle.seek(BLOCK)
            handle.write(full.block_pattern(0, BLOCK, 0))

        verified, mismatches, _ = full._verify_pass(
            str(target), SPAN, BLOCK, 0, 16, None
        )

        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["offset"], BLOCK)
        self.assertEqual(mismatches[0]["found_offset"], 0)
        self.assertIn("wrapping counterfeit", mismatches[0]["reason"])
        self.assertEqual(verified, SPAN - BLOCK)

    def test_mismatch_without_a_valid_header_is_still_reported(self):
        target = make_target(self.root)
        full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )
        with open(target, "r+b") as handle:
            handle.seek(BLOCK)
            handle.write(b"\xff" * BLOCK)

        _verified, mismatches, _ = full._verify_pass(
            str(target), SPAN, BLOCK, 0, 16, None
        )

        # 0xFF bytes decode to 2**64-1, which is not a plausible offset, so it
        # must not be reported as data returned from elsewhere on the device.
        self.assertEqual(len(mismatches), 1)
        self.assertNotIn("found_offset", mismatches[0])
        self.assertIn("differ from the pattern", mismatches[0]["reason"])

    def test_zeroed_block_is_corruption_not_a_wrap(self):
        # A bad sector returning zeros decodes as offset 0. Classifying that as
        # a wrap produced a "Fake capacity detected" verdict for what is
        # ordinary corruption, so the whole block must match the pattern for
        # that offset before it counts.
        target = make_target(self.root)
        result = full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )
        self.assertEqual(result["status"], "ok")
        with open(target, "r+b") as handle:
            handle.seek(BLOCK)
            handle.write(b"\x00" * BLOCK)

        _verified, mismatches, _ = full._verify_pass(
            str(target), SPAN, BLOCK, 0, 16, None
        )

        self.assertEqual(len(mismatches), 1)
        self.assertNotIn("found_offset", mismatches[0])
        self.assertIn("differ from the pattern", mismatches[0]["reason"])


class Options(TempDirCase):
    def test_limit_bytes_shortens_the_span(self):
        target = make_target(self.root)
        result = full.run_full_capacity(
            make_device(target),
            force=True,
            yes=True,
            block_size=BLOCK,
            limit_bytes=BLOCK * 2,
        )

        self.assertEqual(result["details"]["tested_span_bytes"], BLOCK * 2)
        self.assertEqual(result["details"]["bytes_written"], BLOCK * 2)
        self.assertEqual(result["status"], "ok")

    def test_seed_changes_the_written_data(self):
        target = make_target(self.root)
        full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK, seed=7
        )

        self.assertEqual(target.read_bytes()[:BLOCK], full.block_pattern(0, BLOCK, 7))

    def test_progress_is_reported_for_both_passes(self):
        target = make_target(self.root)
        seen: list[tuple[int, int, str]] = []
        full.run_full_capacity(
            make_device(target),
            force=True,
            yes=True,
            block_size=BLOCK,
            progress=lambda done, total, phase: seen.append((done, total, phase)),
        )

        self.assertTrue(seen)
        self.assertEqual(seen[-1], (SPAN, SPAN, "verify"))
        # Write pass plus verify pass.
        self.assertEqual(len(seen), 2 * (SPAN // BLOCK))
        # Each pass names itself, so a caller can account for them separately
        # instead of watching progress reach 100% and then fall back to zero.
        self.assertEqual({phase for _done, _total, phase in seen}, {"write", "verify"})

    def test_a_failed_cache_drop_is_reported_not_swallowed(self):
        # Dropping the page cache is what makes the verify pass test the card
        # rather than RAM. Discarding the failure meant a run whose evidence
        # was worthless still reported a clean verify.
        target = make_target(self.root)
        with patch(
            "os.posix_fadvise", side_effect=OSError(1, "Operation not permitted")
        ):
            result = full.run_full_capacity(
                make_device(target), force=True, yes=True, block_size=BLOCK
            )

        self.assertTrue(
            any("page cache" in warning for warning in result["warnings"]),
            result["warnings"],
        )

    def test_a_failed_cache_drop_does_not_fail_the_run(self):
        # The write may be perfectly sound; what is in doubt is the evidence.
        target = make_target(self.root)
        with patch(
            "os.posix_fadvise", side_effect=OSError(1, "Operation not permitted")
        ):
            result = full.run_full_capacity(
                make_device(target), force=True, yes=True, block_size=BLOCK
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["issues"], [])

    def test_a_clean_run_warns_about_nothing(self):
        target = make_target(self.root)
        result = full.run_full_capacity(
            make_device(target), force=True, yes=True, block_size=BLOCK
        )
        self.assertEqual(result["warnings"], [])

    def test_an_fsync_failure_is_an_issue_not_a_warning(self):
        # Different problem, different channel: the data never reached the
        # card, which is a failure rather than weakened evidence.
        target = make_target(self.root)
        with patch("os.fsync", side_effect=OSError(5, "Input/output error")):
            result = full.run_full_capacity(
                make_device(target), force=True, yes=True, block_size=BLOCK
            )

        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fsync" in issue for issue in result["issues"]))
        self.assertEqual(result["warnings"], [])

    def test_a_failed_fsync_suppresses_the_cache_warning(self):
        # Both failing at once is the case the test above only covered by
        # accident. The run has already failed for a stronger reason, so
        # doubting the evidence for data that never arrived is noise.
        target = make_target(self.root)
        with (
            patch("os.fsync", side_effect=OSError(5, "Input/output error")),
            patch(
                "os.posix_fadvise", side_effect=OSError(1, "Operation not permitted")
            ),
        ):
            result = full.run_full_capacity(
                make_device(target), force=True, yes=True, block_size=BLOCK
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["warnings"], [])

    def test_max_mismatches_caps_the_report(self):
        target = make_target(self.root)
        # Nothing was written, so every block mismatches.
        _verified, mismatches, _ = full._verify_pass(
            str(target), SPAN, BLOCK, 0, 2, None
        )

        self.assertEqual(len(mismatches), 2)

    def test_block_size_below_the_header_is_rejected(self):
        # A block smaller than the 16-byte header cannot carry its own offset.
        # `block_pattern` used to return the full header regardless, so a
        # 1-byte block wrote 16 bytes while the caller counted one.
        target = make_target(self.root)
        for block_size in (0, 1, 8, 15):
            with self.subTest(block_size=block_size):
                with pytest.raises(ArgumentError):
                    full.run_full_capacity(
                        make_device(target),
                        force=True,
                        yes=True,
                        block_size=block_size,
                    )

    def test_non_positive_limit_is_an_argument_error(self):
        # These used to fall through to "Device reports no capacity to test",
        # which blamed the device for a bad flag and hid the actual value.
        target = make_target(self.root)
        for limit in (0, -1, -4096):
            with self.subTest(limit=limit):
                with pytest.raises(ArgumentError) as excinfo:
                    full.run_full_capacity(
                        make_device(target), force=True, yes=True, limit_bytes=limit
                    )
                self.assertEqual(excinfo.value.details["limit_bytes"], limit)

    def test_zero_capacity_device_is_still_a_device_error(self):
        # A device that reports no capacity is not an argument problem.
        target = make_target(self.root, size_bytes=BLOCK)
        with pytest.raises(RuntimeIOError):
            full.run_full_capacity(
                make_device(target, size_bytes=0), force=True, yes=True
            )

    def test_block_pattern_never_exceeds_the_requested_size(self):
        for size in (16, 17, 31, 32, 4096):
            with self.subTest(size=size):
                self.assertEqual(len(full.block_pattern(0, size, 0)), size)
        with pytest.raises(ArgumentError):
            full.block_pattern(0, 15, 0)

    def test_fsync_failure_is_reported(self):
        # Buffered writes hide media errors until the flush. Swallowing it let
        # cached pages satisfy the verify reads and the test return ok.
        target = make_target(self.root)

        def failing_fsync(_fd: int) -> None:
            raise OSError(5, "Input/output error")

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(os, "fsync", failing_fsync)
            result = full.run_full_capacity(
                make_device(target), force=True, yes=True, block_size=BLOCK
            )

        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("fsync failed" in issue for issue in result["issues"]))

    def test_zero_capacity_is_rejected(self):
        target = make_target(self.root, size_bytes=BLOCK)
        with pytest.raises(RuntimeIOError):
            full.run_full_capacity(
                make_device(target, size_bytes=0), force=True, yes=True
            )
