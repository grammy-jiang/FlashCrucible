from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

from tfqa.core import paths
from tfqa.core.errors import ArgumentError
from tfqa.core.models import ConfigModel

try:
    toml_impl = import_module("tomllib")
except ModuleNotFoundError:
    toml_impl = import_module("tomli")

toml = toml_impl

WORKFLOWS_FILENAME = "structured-combos.toml"
DEFAULT_WORKFLOWS_DIR = paths.DEFAULT_WORKFLOWS_DIR


def _resolve_workflows_path(config: ConfigModel) -> Path:
    return paths.workflows_dir(config) / WORKFLOWS_FILENAME


def _load_raw_combos(config: ConfigModel) -> list[dict[str, Any]]:
    target_path = _resolve_workflows_path(config)
    if not target_path.exists():
        raise ArgumentError(
            message="Structured workflow definitions missing",
            details={"path": str(target_path)},
        )
    try:
        with target_path.open("rb") as fh:
            data = toml.load(fh)
    except OSError as exc:
        raise ArgumentError(
            message="Unable to read structured workflow definitions",
            details={"path": str(target_path), "error": str(exc)},
        )
    raw_combos = data.get("combos")
    if not isinstance(raw_combos, list):
        return []
    parsed: list[dict[str, Any]] = []
    for combo in cast(Sequence[Any], raw_combos):
        if isinstance(combo, dict):
            parsed.append(cast(dict[str, Any], combo))
    return parsed


class WorkloadCombo:
    """Named structured workload combo with a curated stage plan."""

    def __init__(
        self,
        name: str,
        stages: Iterable[str],
        description: str | None = None,
        profile: str | None = None,
        image_options: dict[str, Any] | None = None,
    ) -> None:
        normalized = [stage.strip() for stage in stages if stage and str(stage).strip()]
        if not normalized:
            raise ArgumentError(
                message="Structured workload combo must declare at least one stage",
                details={"combo": name},
            )
        self.name = name
        self.stages = tuple(normalized)
        self.description = description
        self.profile = profile
        self.image_options = image_options

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkloadCombo":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ArgumentError(
                message="Combo entry is missing a name",
                details={"entry": raw},
            )
        raw_stages = raw.get("stages")
        stages: list[str] = []
        if isinstance(raw_stages, Sequence):
            stages = [
                str(stage)
                for stage in cast(Sequence[Any], raw_stages)
                if stage is not None
            ]
        description = raw.get("description")
        profile = raw.get("profile")
        image_options = None
        image_section = raw.get("image")
        if isinstance(image_section, dict):
            image_options = {
                str(k): v for k, v in cast(dict[str, Any], image_section).items()
            }
        return cls(
            name=name,
            stages=stages,
            description=str(description) if description is not None else None,
            profile=str(profile) if profile else None,
            image_options=image_options,
        )


def list_combos(config: ConfigModel) -> list[WorkloadCombo]:
    raw = _load_raw_combos(config)
    return [WorkloadCombo.from_dict(combo) for combo in raw]


def load_combo(name: str, config: ConfigModel) -> WorkloadCombo:
    combos = list_combos(config)
    for combo in combos:
        if combo.name == name:
            return combo
    raise ArgumentError(
        message=f"Structured workload combo not found: {name}",
        details={"combo": name},
    )
