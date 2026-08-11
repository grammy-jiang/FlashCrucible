"""Where FlashCrucible finds its bundled data.

One authoritative home for the profile, workflow, and schema directories.
These used to be resolved independently in three modules with three different
`Path(__file__).parents[...]` expressions, two of which pointed at a directory
that did not exist, so `profiles` listed nothing, `combos` errored, and
`pipeline` failed on "Profile 'default' not found" out of the box.

The data ships inside the package (`tfqa/data/`) so an installed wheel can find
it too; resolving relative to the repository root only ever worked from a
source checkout.
"""

from __future__ import annotations

from pathlib import Path

from tfqa.core.models import ConfigModel

PACKAGE_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

DEFAULT_PROFILES_DIR = PACKAGE_DATA_DIR / "profiles"
DEFAULT_WORKFLOWS_DIR = PACKAGE_DATA_DIR / "workflows"
DEFAULT_SCHEMAS_DIR = PACKAGE_DATA_DIR / "schemas" / "json"


def _resolve(override: Path | str | None, default: Path) -> Path:
    return Path(override) if override else default


def profiles_dir(config: ConfigModel | None = None) -> Path:
    """Directory holding endurance profile presets."""

    return _resolve(getattr(config, "profiles_dir", None), DEFAULT_PROFILES_DIR)


def workflows_dir(config: ConfigModel | None = None) -> Path:
    """Directory holding structured workload combo definitions."""

    return _resolve(getattr(config, "workflows_dir", None), DEFAULT_WORKFLOWS_DIR)


def schemas_dir(config: ConfigModel | None = None) -> Path:
    """Directory holding the JSON schemas that describe the CLI contract."""

    return _resolve(getattr(config, "schemas_dir", None), DEFAULT_SCHEMAS_DIR)
