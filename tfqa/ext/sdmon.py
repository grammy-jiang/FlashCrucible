"""Wrapper for the `sdmon` health utility.

sdmon reads vendor health registers (CMD56) from industrial SD cards and prints
JSON. The previous implementation ran the tool, checked its exit code, threw the
output away, and returned numbers derived from `hash(device_path)` -- so the
reported wear changed on every run and had nothing to do with the card. This
parses what sdmon actually printed, and raises when there is nothing to parse.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, TypedDict

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError


SdmonHealth = TypedDict(
    "SdmonHealth",
    {
        "life_used_percent": int,
        "power_on_count": int,
        "read_error_count": int,
        "write_error_count": int,
        "spare_block_count": int,
        "temperature_celsius": int,
        "manufacture_date": str,
        "sdmon_version": str,
        "source": str,
        "raw": dict[str, Any],
    },
    total=False,
)


_SDMON_CMD = "sdmon"

# sdmon's key names vary by vendor generation, so each metric accepts the
# spellings seen in the wild. Order is preference order.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "life_used_percent": (
        "healthStatusPercentUsed",
        "health_status_percent_used",
        "percentLifetimeUsed",
        "lifeUsedPercent",
    ),
    "power_on_count": ("powerOnTimes", "power_on_times", "powerCycleCount"),
    "read_error_count": ("readErrorCount", "read_error_count"),
    "write_error_count": ("writeErrorCount", "write_error_count"),
    # Remaining reserve, not a failure count. Mapping it onto read_error_count
    # inverted its meaning: a healthy card with a large spare pool looked like
    # it had thousands of read errors, in snapshots and in trends.
    "spare_block_count": ("spareBlockCount", "spare_block_count"),
    "temperature_celsius": ("temperature", "temperatureCelsius"),
    "manufacture_date": ("manufactureYM", "manufacture_ym", "manufactureDate"),
}


def _find_tool() -> str:
    path = shutil.which(_SDMON_CMD)
    if not path:
        raise ToolNotFoundError(_SDMON_CMD)
    return path


def _probe_version(executable: str) -> str | None:
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.stdout:
            return proc.stdout.strip().splitlines()[0]
        if proc.stderr:
            return proc.stderr.strip().splitlines()[0]
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip().rstrip("%"), 0)
        except ValueError:
            return None
    return None


def parse_output(stdout: str) -> dict[str, Any]:
    """Parse sdmon's JSON output into a raw mapping."""

    text = stdout.strip()
    if not text:
        raise ValueError("sdmon produced no output")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"sdmon output was {type(parsed).__name__}, expected object")
    return parsed


def map_fields(raw: dict[str, Any]) -> SdmonHealth:
    """Map sdmon's vendor-specific keys onto the shared health metric names."""

    mapped = SdmonHealth()
    for metric, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if alias not in raw:
                continue
            value = raw[alias]
            if metric == "manufacture_date":
                if isinstance(value, str) and value.strip():
                    mapped["manufacture_date"] = value.strip()
                    break
                continue
            coerced = _coerce_int(value)
            if coerced is not None:
                mapped[metric] = coerced  # type: ignore[literal-required]
                break
    return mapped


def read_health(device_path: str, *, timeout_seconds: float = 30.0) -> SdmonHealth:
    """Return industrial health information for supported cards."""

    executable = _find_tool()
    version = _probe_version(executable)

    try:
        proc = subprocess.run(
            [executable, device_path],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "sdmon timed out",
            timeout_seconds,
            {"device_path": device_path, "tool": _SDMON_CMD},
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeIOError(
            "sdmon returned a non-zero exit code",
            {
                "device_path": device_path,
                "exit_code": exc.returncode,
                "stderr": (exc.stderr or "").strip(),
            },
        ) from exc
    except FileNotFoundError as exc:  # pragma: no cover (guard)
        raise ToolNotFoundError(_SDMON_CMD) from exc

    try:
        raw = parse_output(proc.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeIOError(
            "Could not parse sdmon output",
            {
                "device_path": device_path,
                "error": str(exc),
                "stdout": proc.stdout.strip()[:500],
            },
        ) from exc

    health = map_fields(raw)
    if not health:
        raise RuntimeIOError(
            "sdmon reported no recognised health fields",
            {
                "device_path": device_path,
                "keys": sorted(raw)[:20],
                "hint": "The card may not implement the vendor CMD56 registers.",
            },
        )

    health["source"] = "sdmon"
    health["raw"] = raw
    if version:
        health["sdmon_version"] = version
    return health
