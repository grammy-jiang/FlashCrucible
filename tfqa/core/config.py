"""Configuration loader for tfqa.

Loads configuration from multiple sources with precedence:

    defaults < /etc/tfqa/config.toml < ~/.config/tfqa/config.toml < ./tfqa.toml < env vars < cli_overrides

Exports:
  - load_config(cli_overrides: dict|None = None) -> ConfigModel
  - find_config_files() -> list[Path]

The loader is intentionally minimal: it reads TOML files if present and merges
maps shallowly. Environment variables prefixed with TFQA_ are mapped to config
keys (e.g., TFQA_LOG_DIR -> log_dir).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, cast

try:
    import tomllib as toml
except Exception:  # pragma: no cover - fallback for older envs
    import tomli as toml  # type: ignore

from tfqa.core.models import ConfigModel


DEFAULT_CONFIG_PATHS: list[Path] = [
    Path("/etc/tfqa/config.toml"),
    Path.home() / ".config" / "tfqa" / "config.toml",
    Path.cwd() / "tfqa.toml",
]


def _read_toml_file(path: Path | str) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            data = toml.load(fh)
            if isinstance(data, dict):
                return cast(dict[str, Any], data)
    except Exception:
        return None
    return None


def _recursive_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = base.copy()
    for key, value in overrides.items():
        if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
            current = cast(dict[str, Any], merged[key])
            merged[key] = _recursive_merge(current, cast(dict[str, Any], value))
        else:
            merged[key] = value
    return merged


def _env_overrides() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith("TFQA_"):
            continue
        cfg_key = key[len("TFQA_") :].lower()
        out[cfg_key] = value
    return out


def _normalize_paths(values: dict[str, Any]) -> dict[str, Any]:
    normalized = values.copy()
    for key in ("log_dir", "profiles_dir", "schemas_dir"):
        val = normalized.get(key)
        if isinstance(val, (str, Path)):
            try:
                normalized[key] = Path(val).expanduser()
            except Exception:
                pass
    return normalized


def find_config_files() -> list[Path]:
    """Return existing config files from the standard search locations."""
    return [p for p in DEFAULT_CONFIG_PATHS if p.exists()]


def load_config(
    *,
    cli_overrides: dict[str, Any] | None = None,
    config_paths: Iterable[Path | str] | None = None,
) -> ConfigModel:
    """Load and merge configuration from defaults, files, env and CLI.

    Precedence (low -> high): DEFAULT_CONFIG_PATHS < config_paths < env vars < CLI overrides
    """

    merged: dict[str, Any] = {}

    def _merge_path(p: Path | str) -> None:  # noqa: D401 - narrow helper
        nonlocal merged
        data = _read_toml_file(p)
        if data:
            merged = _recursive_merge(merged, data)

    for path in DEFAULT_CONFIG_PATHS:
        _merge_path(path)

    if config_paths:
        for override_path in config_paths:
            _merge_path(override_path)

    merged = _recursive_merge(merged, _env_overrides())

    if cli_overrides:
        merged = _recursive_merge(merged, cli_overrides)

    normalized = _normalize_paths(merged)
    return ConfigModel.model_validate(normalized)
