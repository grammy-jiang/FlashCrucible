import subprocess
import unittest
from typing import Any
from unittest.mock import patch

from tfqa.core.errors import (
    PermissionError as TFQAPermissionError,
    TimeoutError,
    ToolNotFoundError,
)
from tfqa.ext.fsck import FsckResult, run_fsck


class TestFsck(unittest.TestCase):
    def test_fsck_tool_missing(self) -> None:
        with patch("tfqa.ext.fsck.shutil.which", return_value=None):
            with self.assertRaises(ToolNotFoundError):
                run_fsck("/dev/fake")

    def test_fsck_success(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["fsck", "-n", "-V", "/dev/fake"],
            returncode=0,
            stdout="fsck 1.46.5 clean\n",
            stderr="",
        )

        def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return completed

        with (
            patch("tfqa.ext.fsck.shutil.which", return_value="/usr/sbin/fsck"),
            patch("tfqa.ext.fsck.subprocess.run", side_effect=_fake_run),
        ):
            result = run_fsck("/dev/fake")

        self.assertIsInstance(result, FsckResult)
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.clean)
        self.assertEqual(result.returncode, 0)

    def test_fsck_warning(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["fsck", "-n", "-V", "/dev/fake"],
            returncode=1,
            stdout="fsck 1.46.5 fixed errors\n",
            stderr="",
        )

        def _fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return completed

        with (
            patch("tfqa.ext.fsck.shutil.which", return_value="/usr/sbin/fsck"),
            patch("tfqa.ext.fsck.subprocess.run", side_effect=_fake_run),
        ):
            result = run_fsck("/dev/fake")

        self.assertEqual(result.status, "ok")
        self.assertTrue(result.errors_fixed)

    def test_fsck_timeout(self) -> None:
        def raise_timeout(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

        with (
            patch("tfqa.ext.fsck.shutil.which", return_value="/usr/sbin/fsck"),
            patch("tfqa.ext.fsck.subprocess.run", side_effect=raise_timeout),
        ):
            with self.assertRaises(TimeoutError):
                run_fsck("/dev/fake")

    def test_fsck_permission_error(self) -> None:
        def raise_permission(*args: Any, **kwargs: Any) -> None:
            raise PermissionError("denied")

        with (
            patch("tfqa.ext.fsck.shutil.which", return_value="/usr/sbin/fsck"),
            patch("tfqa.ext.fsck.subprocess.run", side_effect=raise_permission),
        ):
            with self.assertRaises(TFQAPermissionError):
                run_fsck("/dev/fake")
