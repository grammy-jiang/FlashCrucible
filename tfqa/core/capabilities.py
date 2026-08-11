"""Probe external tool availability and versions.

This module exposes a small API for discovering external wrapper tools and
reporting their availability in a machine-friendly form.

Public API:
  - probe_capabilities(tool_names: list[str] | None = None) -> dict
  - check_tool(name: str) -> dict
  - _clear_cache() -> None  # test-only

The implementation is intentionally lightweight and easy to mock in tests.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Dict, Optional

import platform

from tfqa.core.models import ToolCapability, Capabilities


# Every external binary a command declares in `describe`. If `describe` names a
# tool this list omits, a caller cannot check for it before running the command.
_DEFAULT_TOOLS = [
    "f3probe",
    "mmc",
    "badblocks",
    "fio",
    "sdmon",
    "dd",
    "cmp",
    "fsck",
]

_cache: Dict[str, ToolCapability] = {}


def _get_version_from_tool(
    path: str, version_arg: str = "--version", timeout: int = 3
) -> Optional[str]:
    """Attempt to run the tool with --version and capture a version string.

    Returns the first line of stdout/stderr if successful, otherwise None.
    """
    try:
        proc = subprocess.run(
            [path, version_arg], capture_output=True, text=True, timeout=timeout
        )
        # Prefer stdout, fallback to stderr
        out = proc.stdout.strip() or proc.stderr.strip()
        if not out:
            return None
        # Return first non-empty line
        for line in out.splitlines():
            if line.strip():
                return line.strip()
        return None
    except Exception:
        # Any error probing the tool should be treated as 'no version found'
        return None


def probe_capabilities(tool_names: list[str] | None = None) -> Capabilities:
    """Probe system for known tools and return a Capabilities model.

    Args:
        tool_names: Optional list of tool executable names to probe. If None,
            probes a default set.

    Returns:
        Capabilities dataclass instance describing found tools and platform.
    """
    global _cache

    tools = tool_names or _DEFAULT_TOOLS
    external_tools: Dict[str, ToolCapability] = {}

    for name in tools:
        if name in _cache:
            external_tools[name] = _cache[name]
            continue

        path = shutil.which(name)
        if path:
            version = _get_version_from_tool(path)
            cap = ToolCapability(name=name, available=True, version=version, path=path)
        else:
            cap = ToolCapability(name=name, available=False)
        _cache[name] = cap
        external_tools[name] = cap

    caps = Capabilities(
        version="0.1.0",
        platform=f"{platform.system()} {platform.machine()}",
        external_tools=dict(external_tools.items()),
        features={},
    )

    return caps


def check_tool(name: str) -> ToolCapability:
    """Return cached ToolCapability or probe a single tool.

    Args:
        name: Executable name

    Returns:
        ToolCapability dataclass
    """
    if name in _cache:
        return _cache[name]
    caps = probe_capabilities([name])
    return caps.external_tools[name]


def _clear_cache() -> None:
    """Clear the internal cache. Intended for tests only."""
    _cache.clear()
