"""Regression tests for bundled-data resolution and packaging.

Three modules used to resolve the data directories independently, with three
different `Path(__file__).parents[...]` expressions. Two pointed at a directory
that never existed, so out of the box `profiles` listed nothing, `combos`
errored, and `pipeline` failed on "Profile 'default' not found".

The data also sat outside the package and was excluded from the wheel, so an
installed CLI could not find any of it, and `click` was imported without being
declared as a dependency, so a fresh install crashed before running anything.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path
from unittest import TestCase

import tfqa
from tfqa.core import paths
from tfqa.core.models import ConfigModel
from tfqa.orchestration import profile as profile_mod
from tfqa.orchestration import workflows as workflows_mod

PACKAGE_ROOT = Path(tfqa.__file__).resolve().parent


class BundledDataLocation(TestCase):
    def test_data_lives_inside_the_package(self):
        # Anything outside the package is not shipped in the wheel, which is
        # what made the installed CLI unusable.
        self.assertTrue(paths.PACKAGE_DATA_DIR.is_relative_to(PACKAGE_ROOT))

    def test_every_default_directory_exists(self):
        for name, directory in (
            ("profiles", paths.DEFAULT_PROFILES_DIR),
            ("workflows", paths.DEFAULT_WORKFLOWS_DIR),
            ("schemas", paths.DEFAULT_SCHEMAS_DIR),
        ):
            with self.subTest(directory=name):
                self.assertTrue(directory.is_dir(), f"missing: {directory}")

    def test_every_default_directory_has_content(self):
        self.assertTrue(list(paths.DEFAULT_PROFILES_DIR.glob("*.toml")))
        self.assertTrue(
            (paths.DEFAULT_WORKFLOWS_DIR / workflows_mod.WORKFLOWS_FILENAME).is_file()
        )
        self.assertTrue(list(paths.DEFAULT_SCHEMAS_DIR.glob("*.json")))

    def test_the_resolvers_agree_on_one_root(self):
        # The bug was three modules disagreeing about where the data lives.
        for directory in (
            paths.DEFAULT_PROFILES_DIR,
            paths.DEFAULT_WORKFLOWS_DIR,
            paths.DEFAULT_SCHEMAS_DIR,
        ):
            with self.subTest(directory=directory.name):
                self.assertTrue(directory.is_relative_to(paths.PACKAGE_DATA_DIR))

    def test_orchestration_modules_use_the_shared_defaults(self):
        self.assertEqual(profile_mod.DEFAULT_PROFILES_DIR, paths.DEFAULT_PROFILES_DIR)
        self.assertEqual(
            workflows_mod.DEFAULT_WORKFLOWS_DIR, paths.DEFAULT_WORKFLOWS_DIR
        )


class ConfigOverrides(TestCase):
    def test_config_wins_over_the_default(self):
        custom = Path("/tmp/tfqa-custom")
        config = ConfigModel(
            profiles_dir=custom / "profiles",
            workflows_dir=custom / "workflows",
            schemas_dir=custom / "schemas",
        )
        self.assertEqual(paths.profiles_dir(config), custom / "profiles")
        self.assertEqual(paths.workflows_dir(config), custom / "workflows")
        self.assertEqual(paths.schemas_dir(config), custom / "schemas")

    def test_empty_config_falls_back_to_the_defaults(self):
        config = ConfigModel()
        self.assertEqual(paths.profiles_dir(config), paths.DEFAULT_PROFILES_DIR)
        self.assertEqual(paths.workflows_dir(config), paths.DEFAULT_WORKFLOWS_DIR)
        self.assertEqual(paths.schemas_dir(config), paths.DEFAULT_SCHEMAS_DIR)

    def test_no_config_falls_back_to_the_defaults(self):
        self.assertEqual(paths.profiles_dir(), paths.DEFAULT_PROFILES_DIR)
        self.assertEqual(paths.workflows_dir(), paths.DEFAULT_WORKFLOWS_DIR)
        self.assertEqual(paths.schemas_dir(), paths.DEFAULT_SCHEMAS_DIR)


class DeclaredDependencies(TestCase):
    def _declared(self) -> set[str]:
        requirements = metadata.requires("flashcrucible") or []
        return {
            re.split(r"[<>=!~\[;( ]", req, maxsplit=1)[0].strip().lower()
            for req in requirements
        }

    def test_click_is_declared(self):
        # tfqa.cli.main imports click directly to introspect the command tree.
        # Typer 0.27 dropped click from its own dependencies, so relying on it
        # transitively left fresh installs crashing on `import click`.
        self.assertIn("click", self._declared())

    def test_directly_imported_runtime_packages_are_declared(self):
        declared = self._declared()
        for package in ("typer", "click", "rich", "pydantic", "jsonschema"):
            with self.subTest(package=package):
                self.assertIn(package, declared)


class ProfileLoading(TestCase):
    def test_the_default_profile_loads_without_any_override(self):
        # `pipeline` fails before it starts if this does not resolve.
        loaded = profile_mod.load_profile("default", ConfigModel())
        self.assertEqual(loaded.name, "default")

    def test_combos_load_without_any_override(self):
        combos = workflows_mod.list_combos(ConfigModel())
        self.assertTrue(combos)
