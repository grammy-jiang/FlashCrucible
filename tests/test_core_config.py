import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tfqa.core import config as cfg_mod
from tfqa.core.models import ConfigModel


def write_toml(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


class CoreConfigTest(unittest.TestCase):
    def test_find_config_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            a = base / "a.toml"
            b = base / "b.toml"
            write_toml(a, "log_dir = '/tmp/a'\n")

            original = cfg_mod.DEFAULT_CONFIG_PATHS
            cfg_mod.DEFAULT_CONFIG_PATHS = [a, b]

            try:
                files = cfg_mod.find_config_files()
                self.assertIn(a, files)
                self.assertNotIn(b, files)
            finally:
                cfg_mod.DEFAULT_CONFIG_PATHS = original

    def test_load_config_file_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            etc = base / "etc" / "config.toml"
            home = base / "home" / ".config" / "tfqa" / "config.toml"
            cwd = base / "tfqa.toml"

            write_toml(etc, "log_dir = '/etc/log'\n")
            write_toml(home, "profiles_dir = 'home_profiles'\n")
            write_toml(cwd, "log_dir = '/cwd/log'\n")

            original = cfg_mod.DEFAULT_CONFIG_PATHS
            cfg_mod.DEFAULT_CONFIG_PATHS = [etc, home, cwd]

            with (
                mock.patch("pathlib.Path.home", return_value=base / "home"),
                mock.patch.dict(os.environ, {"TFQA_IGNORE_ME": "1"}, clear=False),
            ):
                try:
                    cfg = cfg_mod.load_config()
                    self.assertIsInstance(cfg, ConfigModel)
                    self.assertIsNotNone(cfg.log_dir)
                    self.assertEqual(cfg.log_dir, Path("/cwd/log"))
                    self.assertEqual(
                        getattr(cfg, "profiles_dir"), Path("home_profiles")
                    )
                finally:
                    cfg_mod.DEFAULT_CONFIG_PATHS = original

    def test_env_and_cli_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            a = base / "a.toml"
            write_toml(a, "log_dir = '/file/log'\n")

            original = cfg_mod.DEFAULT_CONFIG_PATHS
            cfg_mod.DEFAULT_CONFIG_PATHS = [a]

            with mock.patch.dict(os.environ, {"TFQA_LOG_DIR": "/env/log"}, clear=False):
                try:
                    cfg_env = cfg_mod.load_config()
                    self.assertIsNotNone(cfg_env.log_dir)
                    self.assertEqual(str(cfg_env.log_dir), "/env/log")

                    cfg_cli = cfg_mod.load_config(cli_overrides={"log_dir": "/cli/log"})
                    self.assertIsNotNone(cfg_cli.log_dir)
                    self.assertEqual(str(cfg_cli.log_dir), "/cli/log")
                finally:
                    cfg_mod.DEFAULT_CONFIG_PATHS = original

    def test_recursive_merge_supports_nested_dicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "base.toml"
            override = Path(tmpdir) / "override.toml"
            write_toml(
                base, "settings = { nested = { timeout = 5, enabled = true } }\n"
            )
            write_toml(
                override, "settings = { nested = { enabled = false, mode = 'lab' } }\n"
            )

            original = cfg_mod.DEFAULT_CONFIG_PATHS
            cfg_mod.DEFAULT_CONFIG_PATHS = [base]

            try:
                cfg = cfg_mod.load_config(config_paths=[override])
                nested = cfg.model_dump().get("settings", {}).get("nested")
                self.assertEqual(
                    nested,
                    {
                        "timeout": 5,
                        "enabled": False,
                        "mode": "lab",
                    },
                )
            finally:
                cfg_mod.DEFAULT_CONFIG_PATHS = original

    def test_load_config_ignores_invalid_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            bad = base / "bad.toml"
            write_toml(bad, "not valid [\n")

            original = cfg_mod.DEFAULT_CONFIG_PATHS
            cfg_mod.DEFAULT_CONFIG_PATHS = [bad]

            try:
                cfg = cfg_mod.load_config()
                self.assertIsInstance(cfg, ConfigModel)
            finally:
                cfg_mod.DEFAULT_CONFIG_PATHS = original
