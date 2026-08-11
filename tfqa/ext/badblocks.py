"""Wrapper utilities for invoking badblocks surface scans.

badblocks is run with `-v`, so it reports what it found: the bad block numbers
on stdout and a summary line on stderr. This wrapper used to discard all of it
and return `read_errors: 0` with a `coverage_percent` of 95.0/98.5 and an
`average_latency_ms` of 2.0/3.5 -- none of which came from the tool. A card
with bad blocks was therefore reported clean, on the path where badblocks was
installed and working.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from typing import Dict, List, Literal

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError

_BADBLOCKS_TOOL = "badblocks"

# "Pass completed, 3 bad blocks found. (1/2/0 errors)"
_SUMMARY = re.compile(
    r"(?P<bad>\d+)\s+bad\s+blocks?\s+found\.?"
    r"(?:\s*\((?P<read>\d+)/(?P<write>\d+)/(?P<corrupt>\d+)\s+errors\))?",
    re.IGNORECASE,
)
_BLOCK_LINE = re.compile(r"^\s*(\d+)\s*$")

SurfaceMode = Literal["readonly", "destructive"]


def _find_tool() -> str:
    path = shutil.which(_BADBLOCKS_TOOL)
    if not path:
        raise ToolNotFoundError(_BADBLOCKS_TOOL)
    return path


def _build_command(
    mode: SurfaceMode, device_path: str, block_size: int, pass_count: int
) -> List[str]:
    cmd = [_find_tool(), "-s", "-v", "-b", str(block_size), "-p", str(pass_count)]
    if mode == "destructive":
        cmd.append("-w")
    # Nothing is appended for readonly: badblocks does a non-destructive
    # read-only test by default. This used to pass `-n`, which the man page
    # defines as non-destructive read-*write* -- so "readonly" wrote to the
    # card, while the safety guard exempted the mode on the grounds that it
    # does not.
    cmd.append(device_path)
    return cmd


def parse_bad_blocks(stdout: str, stderr: str) -> int:
    """Return how many bad blocks badblocks reported.

    Prefers its own summary line, which is authoritative, and falls back to
    counting the block numbers it listed on stdout.
    """

    match = _SUMMARY.search(stderr) or _SUMMARY.search(stdout)
    if match:
        return int(match.group("bad"))
    return len(_bad_block_numbers(stdout))


def _bad_block_numbers(stdout: str) -> List[int]:
    return [
        int(m.group(1))
        for m in (_BLOCK_LINE.match(line) for line in stdout.splitlines())
        if m
    ]


def _run_badblocks(
    mode: SurfaceMode,
    device_path: str,
    block_size: int,
    pass_count: int,
    timeout_seconds: float,
) -> Dict[str, object]:
    cmd = _build_command(mode, device_path, block_size, pass_count)
    start = datetime.now()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "badblocks timed out",
            timeout_seconds,
            {
                "device_path": device_path,
                "mode": mode,
                "command": exc.cmd,
            },
        ) from exc

    if proc.returncode != 0:
        raise RuntimeIOError(
            "badblocks reported an error",
            {
                "device_path": device_path,
                "mode": mode,
                "exit_code": proc.returncode,
                "stderr": (proc.stderr or "").strip(),
            },
        )

    duration_seconds = (datetime.now() - start).total_seconds()
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    bad_blocks = parse_bad_blocks(stdout, stderr)

    return {
        "mode": mode,
        "pass_count": pass_count,
        "block_size": block_size,
        # badblocks scans the whole device unless given a range, and we give it
        # none, so a run that exited 0 covered all of it. This is the one
        # coverage figure the tool actually supports.
        "coverage_percent": 100.0,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": proc.returncode,
        "read_only": mode == "readonly",
        "duration_seconds": round(duration_seconds, 2),
        "read_errors": bad_blocks,
        "bad_block_numbers": _bad_block_numbers(stdout),
    }


def run_badblocks_readonly(
    device_path: str,
    *,
    block_size: int = 4096,
    pass_count: int = 1,
    timeout_seconds: float = 180.0,
) -> Dict[str, object]:
    """Run badblocks in read-only/non-destructive mode."""
    return _run_badblocks(
        "readonly",
        device_path,
        block_size,
        pass_count,
        timeout_seconds,
    )


def run_badblocks_write(
    device_path: str,
    *,
    block_size: int = 4096,
    pass_count: int = 1,
    timeout_seconds: float = 360.0,
) -> Dict[str, object]:
    """Run badblocks in destructive write-read mode."""
    return _run_badblocks(
        "destructive",
        device_path,
        block_size,
        pass_count,
        timeout_seconds,
    )
