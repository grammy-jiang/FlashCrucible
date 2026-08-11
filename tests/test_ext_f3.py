from __future__ import annotations

import math
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from tfqa.core.errors import (
    RuntimeIOError,
    TimeoutError as TFQATimeoutError,
    ToolNotFoundError,
)
from tfqa.ext.f3 import run_f3probe

SAMPLE_OUTPUT = """
F3 1.8 by Digirati
Real capacity: 121,670.656 MB (0x7482260 sectors)
Fake capacity: NO
"""


def _patched_which() -> str:
    return "/usr/bin/f3probe"


class TestF3Probe(unittest.TestCase):
    def test_run_f3probe_parses_output(self) -> None:
        with patch("tfqa.ext.f3.shutil.which", return_value=_patched_which()):
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = SAMPLE_OUTPUT
            mock_proc.stderr = ""

            with patch(
                "tfqa.ext.f3.subprocess.run", return_value=mock_proc
            ) as mock_run:
                result = run_f3probe("/dev/fake")

            mock_run.assert_called_once()
            self.assertEqual(result["tool"], "f3probe")
            self.assertFalse(result["fake_detected"])
            self.assertEqual(result["real_size_bytes"], 62_549_966_848)

    def test_run_f3probe_detects_fake_capacity(self) -> None:
        with patch("tfqa.ext.f3.shutil.which", return_value=_patched_which()):
            output = SAMPLE_OUTPUT.replace("Fake capacity: NO", "Fake capacity: YES")
            mock_proc = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("tfqa.ext.f3.subprocess.run", return_value=mock_proc):
                result = run_f3probe("/dev/fake")
            self.assertTrue(result["fake_detected"])

    def test_run_f3probe_tool_missing(self) -> None:
        with patch("tfqa.ext.f3.shutil.which", return_value=None):
            with self.assertRaises(ToolNotFoundError):
                run_f3probe("/dev/fake")

    def test_run_f3probe_runtime_error(self) -> None:
        with patch("tfqa.ext.f3.shutil.which", return_value=_patched_which()):
            mock_proc = MagicMock(returncode=2, stdout="", stderr="Permission denied")
            with patch("tfqa.ext.f3.subprocess.run", return_value=mock_proc):
                with self.assertRaises(RuntimeIOError) as cm:
                    run_f3probe("/dev/fake")
            self.assertEqual(cm.exception.details["return_code"], 2)
            self.assertIn("Permission denied", cm.exception.details["stderr"])

    def test_run_f3probe_timeout(self) -> None:
        with patch("tfqa.ext.f3.shutil.which", return_value=_patched_which()):
            with patch(
                "tfqa.ext.f3.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="f3probe", timeout=60),
            ):
                with self.assertRaises(TFQATimeoutError) as cm:
                    run_f3probe("/dev/fake", timeout_seconds=60.0)
        self.assertTrue(
            math.isclose(cm.exception.details["timeout_seconds"], 60.0, rel_tol=1e-9)
        )
