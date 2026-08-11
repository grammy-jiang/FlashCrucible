"""Tests for the badblocks wrapper.

It ran badblocks with `-v` -- which reports the bad blocks it found -- then
discarded the output and returned `read_errors: 0` with a `coverage_percent`
of 95.0/98.5 and an `average_latency_ms` of 2.0/3.5. None of those came from
the tool, so a card with bad blocks was reported clean on the path where
badblocks was installed and working.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError
from tfqa.ext import badblocks

CLEAN_STDERR = """Checking for bad blocks in read-only mode
From block 0 to 30777
Checking for bad blocks (read-only test): done
Pass completed, 0 bad blocks found. (0/0/0 errors)
"""

DIRTY_STDOUT = "1024\n1025\n4096\n"
DIRTY_STDERR = "Pass completed, 3 bad blocks found. (3/0/0 errors)\n"


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["badblocks"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestParseBadBlocks:
    def test_reads_the_summary_line(self) -> None:
        assert badblocks.parse_bad_blocks("", DIRTY_STDERR) == 3

    def test_zero_is_reported_as_zero(self) -> None:
        assert badblocks.parse_bad_blocks("", CLEAN_STDERR) == 0

    def test_falls_back_to_counting_listed_blocks(self) -> None:
        # No summary line, but badblocks listed the blocks it found.
        assert badblocks.parse_bad_blocks(DIRTY_STDOUT, "") == 3

    def test_summary_wins_over_the_listing(self) -> None:
        assert badblocks.parse_bad_blocks(DIRTY_STDOUT, DIRTY_STDERR) == 3

    def test_no_output_means_nothing_found(self) -> None:
        assert badblocks.parse_bad_blocks("", "") == 0


class TestCommandConstruction:
    """`--mode readonly` must not write to the card.

    It used to pass `-n`, which the badblocks man page defines as
    "non-destructive read-write mode". The safety guard exempts readonly scans
    from the mounted-device check on the grounds that they do not write, so
    that flag quietly made the exemption wrong.
    """

    def _cmd(self, mode: str) -> list[str]:
        with patch("shutil.which", return_value="/usr/sbin/badblocks"):
            return badblocks._build_command(mode, "/dev/sdz", 4096, 1)  # type: ignore[arg-type]

    def test_readonly_passes_no_write_flag(self) -> None:
        cmd = self._cmd("readonly")
        assert "-n" not in cmd, "-n is read-write, not read-only"
        assert "-w" not in cmd

    def test_destructive_passes_the_write_flag(self) -> None:
        assert "-w" in self._cmd("destructive")


class TestRunBadblocks:
    def _run(self, stdout: str = "", stderr: str = CLEAN_STDERR):
        with (
            patch("shutil.which", return_value="/usr/sbin/badblocks"),
            patch("subprocess.run", return_value=_completed(stdout, stderr)),
        ):
            return badblocks.run_badblocks_readonly("/dev/sdz")

    def test_reports_the_errors_the_tool_found(self) -> None:
        result = self._run(DIRTY_STDOUT, DIRTY_STDERR)

        # The old wrapper returned 0 here regardless.
        assert result["read_errors"] == 3
        assert result["bad_block_numbers"] == [1024, 1025, 4096]

    def test_a_clean_card_reports_no_errors(self) -> None:
        result = self._run()

        assert result["read_errors"] == 0
        assert result["bad_block_numbers"] == []

    def test_coverage_is_the_only_figure_the_tool_supports(self) -> None:
        # badblocks scans the whole device when given no range, so a completed
        # run covered all of it. The old 95.0/98.5 estimates were invented.
        assert self._run()["coverage_percent"] == 100.0

    def test_latency_is_not_reported_because_nothing_measures_it(self) -> None:
        assert "average_latency_ms" not in self._run()

    def test_missing_tool_raises(self) -> None:
        with patch("shutil.which", return_value=None), pytest.raises(ToolNotFoundError):
            badblocks.run_badblocks_readonly("/dev/sdz")

    def test_non_zero_exit_raises(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/sbin/badblocks"),
            patch(
                "subprocess.run", return_value=_completed(stderr="nope", returncode=1)
            ),
            pytest.raises(RuntimeIOError),
        ):
            badblocks.run_badblocks_readonly("/dev/sdz")

    def test_timeout_raises(self) -> None:
        with (
            patch("shutil.which", return_value="/usr/sbin/badblocks"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(["badblocks"], 1),
            ),
            pytest.raises(TimeoutError),
        ):
            badblocks.run_badblocks_readonly("/dev/sdz")
