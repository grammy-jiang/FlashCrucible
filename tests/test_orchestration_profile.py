"""Tests for the orchestration profile loader."""

from __future__ import annotations

from pathlib import Path
import math
import pytest

from tfqa.core.errors import ArgumentError
from tfqa.core.models import ConfigModel
from tfqa.orchestration import profile as profile_mod
from tfqa.orchestration.profile import EnduranceProfile


def test_load_profile_with_endurance_section(tmp_path: Path) -> None:
    profile_path = tmp_path / "heavy.toml"
    profile_path.write_text(
        """
        name = "heavy"
        description = "Endurance heavy profile"

        [tests.endurance]
        duration_hours = 1
        pass_count = 8
        force = true
        write_pattern = "random"
        """
    )

    cfg = ConfigModel(profiles_dir=tmp_path)
    loaded = profile_mod.load_profile("heavy", cfg)

    assert isinstance(loaded, EnduranceProfile)
    assert loaded.name == "heavy"
    assert math.isclose(loaded.duration_seconds, 3600.0, rel_tol=1e-9)
    assert loaded.pass_count == 8
    assert loaded.force is True
    assert loaded.write_pattern == "random"


def test_load_profile_defaults_when_endurance_section_missing(tmp_path: Path) -> None:
    profile_path = tmp_path / "default.toml"
    profile_path.write_text(
        """
        name = "default"
        description = "Minimal profile"
        """
    )

    cfg = ConfigModel(profiles_dir=tmp_path)
    loaded = profile_mod.load_profile("default", cfg)

    assert math.isclose(loaded.duration_seconds, 60.0, rel_tol=1e-9)
    assert loaded.pass_count == 5
    assert loaded.force is False
    assert loaded.write_pattern == "sequential"


def test_missing_profile_raises(tmp_path: Path) -> None:
    cfg = ConfigModel(profiles_dir=tmp_path)

    with pytest.raises(ArgumentError):
        profile_mod.load_profile("missing", cfg)


def test_invalid_duration_raises(tmp_path: Path) -> None:
    profile_path = tmp_path / "bad.toml"
    profile_path.write_text(
        """
        name = "bad"
        [tests.endurance]
        duration_seconds = 0
        """
    )

    cfg = ConfigModel(profiles_dir=tmp_path)

    with pytest.raises(ArgumentError):
        profile_mod.load_profile("bad", cfg)
