"""Profile helpers for tfqa orchestration flows."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from tfqa.core.errors import ArgumentError
from tfqa.core.models import ConfigModel

try:
    toml_impl = import_module("tomllib")
except ModuleNotFoundError:
    toml_impl = import_module("tomli")

toml: Any = toml_impl

DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[1] / "data" / "profiles"


@dataclass(frozen=True)
class EnduranceProfile:
    """Metadata needed to configure an endurance run."""

    name: str
    description: str | None = None
    duration_seconds: float = 60.0
    pass_count: int = 5
    force: bool = False
    write_pattern: str = "sequential"

    @classmethod
    def from_dict(cls, source: dict[str, Any]) -> "EnduranceProfile":
        tests = cast(dict[str, Any], source.get("tests", {}) or {})
        endurance = cast(dict[str, Any], tests.get("endurance") or {})

        profile_name = source.get("name") or "default"
        description = source.get("description")
        duration_seconds = _extract_duration_seconds(endurance)
        pass_count = _coerce_positive_int("pass_count", endurance.get("pass_count"), 5)
        force = bool(endurance.get("force", False))
        write_pattern = str(endurance.get("write_pattern", "sequential"))

        return cls(
            name=profile_name,
            description=description,
            duration_seconds=duration_seconds,
            pass_count=pass_count,
            force=force,
            write_pattern=write_pattern,
        )


def _extract_duration_seconds(section: dict[str, Any]) -> float:
    if "duration_seconds" in section:
        value = float(section["duration_seconds"])
    elif "duration_minutes" in section:
        value = float(section["duration_minutes"]) * 60
    elif "duration_hours" in section:
        value = float(section["duration_hours"]) * 3600
    else:
        value = 60.0

    if value <= 0:
        raise ArgumentError(
            message="Endurance duration must be positive",
            details={"value": value},
        )
    return value


def _coerce_positive_int(name: str, value: Any | None, default: int) -> int:
    if value is None:
        candidate = default
    else:
        candidate = int(value)
    if candidate <= 0:
        raise ArgumentError(
            message=f"{name} must be a positive integer",
            details={"field": name, "value": candidate},
        )
    return candidate


def _resolve_profiles_dir(config: ConfigModel) -> Path:
    if config.profiles_dir:
        return Path(config.profiles_dir)
    return DEFAULT_PROFILES_DIR


def load_profile(name: str, config: ConfigModel) -> EnduranceProfile:
    """Load an endurance-focused profile by name."""

    profiles_dir = _resolve_profiles_dir(config)
    profile_path = profiles_dir / f"{name}.toml"

    if not profile_path.exists():
        raise ArgumentError(
            message=f"Profile '{name}' not found in {profiles_dir}",
            details={"profile": name, "profiles_dir": str(profiles_dir)},
        )

    try:
        with profile_path.open("rb") as fh:
            data = cast(dict[str, Any], toml.load(fh))
    except OSError as exc:
        raise ArgumentError(
            message=f"Unable to read profile '{name}': {exc}",
            details={"profile": name},
        )

    return EnduranceProfile.from_dict(data)


def list_profiles(config: ConfigModel) -> list[dict[str, Any]]:
    """Return every known profile along with summary metadata."""

    profiles_dir = _resolve_profiles_dir(config)
    if not profiles_dir.exists() or not profiles_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for profile_path in sorted(profiles_dir.glob("*.toml")):
        if not profile_path.is_file():
            continue
        try:
            with profile_path.open("rb") as fh:
                raw = cast(dict[str, Any], toml.load(fh))
        except Exception:
            continue
        try:
            profile = EnduranceProfile.from_dict(raw)
        except ArgumentError:
            continue
        entries.append(
            {
                "name": profile.name,
                "description": profile.description,
                "duration_seconds": profile.duration_seconds,
                "pass_count": profile.pass_count,
                "force": profile.force,
                "write_pattern": profile.write_pattern,
                "path": str(profile_path),
            }
        )
    return entries
