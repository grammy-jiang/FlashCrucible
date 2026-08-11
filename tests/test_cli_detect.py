import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch
from typer.testing import CliRunner

from tfqa.cli.main import app
from tfqa.core.models import DeviceInfo, CLIResponse


def make_device(path: str, name: str = "sdX") -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


class DetectCLITest(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_detect_json(self):
        devices = [make_device("/dev/sdb", "sdb"), make_device("/dev/sdc", "sdc")]
        with patch("tfqa.core.devices.discover_devices", lambda: devices):
            result = self.runner.invoke(app, ["detect", "--output", "json"])
        self.assertEqual(result.exit_code, 0)

        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.command, "detect")
        self.assertIn("devices", resp.data)
        self.assertEqual(len(resp.data["devices"]), 2)

    def test_detect_human(self):
        devices = [make_device("/dev/sdb", "sdb")]
        with patch("tfqa.core.devices.discover_devices", lambda: devices):
            result = self.runner.invoke(app, ["detect"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("1 block devices detected", result.stdout)

    def test_detect_respects_config_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "cli.toml"
            cfg_file.write_text("log_dir = '/tmp/os-log'\n")

            devices = [make_device("/dev/sdb", "sdb")]
            with patch("tfqa.core.devices.discover_devices", lambda: devices):
                result = self.runner.invoke(
                    app, ["--config", str(cfg_file), "detect", "--output", "json"]
                )
            self.assertEqual(result.exit_code, 0)

            resp = CLIResponse.model_validate_json(result.stdout)
            self.assertEqual(resp.data.get("log_dir"), "/tmp/os-log")

    def test_detect_cli_option_overrides_log_dir(self):
        devices = [make_device("/dev/sdb", "sdb")]
        with patch("tfqa.core.devices.discover_devices", lambda: devices):
            result = self.runner.invoke(
                app, ["--log-dir", "/tmp/cli", "detect", "--output", "json"]
            )
        resp = CLIResponse.model_validate_json(result.stdout)
        self.assertEqual(resp.data.get("log_dir"), "/tmp/cli")
