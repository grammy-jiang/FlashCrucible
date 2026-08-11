"""Shared pytest configuration.

Provides `--hermetic`, which hides every external tool from `shutil.which` for
the duration of the run. The suite must pass on a machine with none of them
installed: three CLI tests once passed only because `f3probe` happened to be
present locally, and broke in CI.

    uv run pytest -q --hermetic
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Dict

import pytest

# Every binary the project shells out to, plus the ones the capability probe
# looks for. Hiding all of them is stricter than any real host.
EXTERNAL_TOOLS = frozenset(
    {
        "badblocks",
        "blkdiscard",
        "cmp",
        "dd",
        "e2fsck",
        "f3probe",
        "f3read",
        "f3write",
        "fio",
        "fsck",
        "fsck.exfat",
        "fsck.vfat",
        "hdparm",
        "mmc",
        "sdmon",
        "smartctl",
        "stress-ng",
    }
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--hermetic",
        action="store_true",
        default=False,
        help="Hide every external tool from shutil.which, as on a bare host.",
    )


@pytest.fixture(autouse=True)
def _hide_external_tools(request: pytest.FixtureRequest) -> Iterator[None]:
    if not request.config.getoption("--hermetic"):
        yield
        return

    real_which = shutil.which

    def fake_which(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = str(cmd).rsplit("/", 1)[-1]
        if name in EXTERNAL_TOOLS:
            return None
        return real_which(cmd, *args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(shutil, "which", fake_which)
    try:
        yield
    finally:
        monkeypatch.undo()


# Simple fixtures to be used by tests/templates. Replace with real models when available.


@pytest.fixture
def device_info() -> Dict[str, Any]:
    return {
        "path": "/dev/fake",
        "name": "fake",
        "size_bytes": 128_000_000_000,
        "is_removable": True,
        "is_system_disk": False,
        "mountpoints": [],
    }


@pytest.fixture
def run_context(device_info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": "test-run-123",
        "started_at": datetime.now(),
        "device": device_info,
        "mode": "ai",
    }
