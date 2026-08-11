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


# A description opening with a quote and continuing on the next line: TOML
# forbids a newline inside a basic string. This is exactly how the shipped
# router-telemetry.toml was written, and the loader hid it.
MALFORMED_TOML = 'name = "broken"\ndescription = "First line.\nSecond line."\n'


def test_malformed_toml_raises_argument_error_not_internal(tmp_path: Path) -> None:
    # It used to escape as a bare TOMLDecodeError, which the CLI reported as
    # INTERNAL_ERROR with a parser message and no filename.
    (tmp_path / "broken.toml").write_text(MALFORMED_TOML)
    cfg = ConfigModel(profiles_dir=tmp_path)

    with pytest.raises(ArgumentError) as excinfo:
        profile_mod.load_profile("broken", cfg)

    assert excinfo.value.error_code == "INVALID_ARGUMENT"
    assert "broken.toml" in excinfo.value.details["path"]


def test_list_profiles_reports_a_malformed_profile(tmp_path: Path) -> None:
    # The whole point: a broken file must be visible, not silently absent.
    (tmp_path / "broken.toml").write_text(MALFORMED_TOML)
    (tmp_path / "fine.toml").write_text('name = "fine"\n')

    entries = profile_mod.list_profiles(ConfigModel(profiles_dir=tmp_path))
    by_name = {entry["name"]: entry for entry in entries}

    assert set(by_name) == {"broken", "fine"}
    assert by_name["broken"]["error"]
    assert by_name["broken"]["path"].endswith("broken.toml")
    assert by_name["fine"]["error"] is None


def test_list_profiles_keeps_reading_after_a_broken_file(tmp_path: Path) -> None:
    # Sorted glob puts "a-broken" first; the rest must still be listed.
    (tmp_path / "a-broken.toml").write_text(MALFORMED_TOML)
    for name in ("b-one", "c-two"):
        (tmp_path / f"{name}.toml").write_text(f'name = "{name}"\n')

    entries = profile_mod.list_profiles(ConfigModel(profiles_dir=tmp_path))

    assert [entry["name"] for entry in entries] == ["a-broken", "b-one", "c-two"]


def test_list_profiles_reports_an_invalid_value(tmp_path: Path) -> None:
    # Valid TOML, rejected by EnduranceProfile.from_dict.
    (tmp_path / "zero.toml").write_text(
        'name = "zero"\n[tests.endurance]\nduration_seconds = 0\n'
    )

    (entry,) = profile_mod.list_profiles(ConfigModel(profiles_dir=tmp_path))

    assert entry["name"] == "zero"
    assert entry["error"]


def test_every_bundled_profile_parses() -> None:
    # Guards the shipped presets: router-telemetry.toml was broken on disk and
    # nothing noticed.
    entries = profile_mod.list_profiles(ConfigModel())
    broken = {e["name"]: e["error"] for e in entries if e.get("error")}

    assert not broken, f"bundled profiles failed to parse: {broken}"
    assert "router-telemetry" in {e["name"] for e in entries}
