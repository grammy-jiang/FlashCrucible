import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from tfqa.ext.image import run_image_flash
from tfqa.core.errors import TimeoutError, ToolNotFoundError


def _fake_which(tool: str) -> str | None:
    return f"/usr/bin/{tool}"


def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=args[0] if isinstance(args[0], list) else [],
        returncode=0,
        stdout="ok",
        stderr="",
    )


class TestImageFlash(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_image_flash_missing_dd(self) -> None:
        image_path = self.tmp_path / "image.bin"
        image_path.write_text("data")
        with patch("tfqa.ext.image.shutil.which", return_value=None):
            with self.assertRaises(ToolNotFoundError):
                run_image_flash(str(image_path), "/dev/fake", verify=False)

    def test_image_flash_success(self) -> None:
        image_path = self.tmp_path / "image.img"
        image_path.write_text("data")
        with (
            patch("tfqa.ext.image.shutil.which", side_effect=_fake_which),
            patch("tfqa.ext.image.subprocess.run", side_effect=_fake_run),
        ):
            result = run_image_flash(str(image_path), "/dev/test")

        self.assertEqual(result["status"], "ok")
        metrics = cast(dict[str, Any], result["metrics"])
        self.assertEqual(metrics["write_returncode"], 0)
        details = cast(dict[str, Any], result["details"])
        self.assertTrue(details["verify_match"])

    def test_image_flash_verify_failure(self) -> None:
        image_path = self.tmp_path / "image.img"
        image_path.write_text("data")

        def verify_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[0][0] == "dd":
                return subprocess.CompletedProcess(
                    args=args[0], returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(
                args=args[0], returncode=1, stdout="", stderr="mismatch"
            )

        with (
            patch("tfqa.ext.image.shutil.which", side_effect=_fake_which),
            patch("tfqa.ext.image.subprocess.run", side_effect=verify_run),
        ):
            result = run_image_flash(str(image_path), "/dev/test")

        self.assertEqual(result["status"], "failed")
        details = cast(dict[str, Any], result["details"])
        self.assertFalse(details["verify_match"])

    def test_image_flash_timeout(self) -> None:
        image_path = self.tmp_path / "image.img"
        image_path.write_text("data")

        def raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=5)

        with (
            patch("tfqa.ext.image.shutil.which", side_effect=_fake_which),
            patch("tfqa.ext.image.subprocess.run", side_effect=raise_timeout),
        ):
            with self.assertRaises(TimeoutError):
                run_image_flash(str(image_path), "/dev/test")
