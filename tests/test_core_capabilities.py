"""Tests for tfqa.core.capabilities."""

from __future__ import annotations

from importlib import reload as reload_module
from unittest import TestCase
from unittest.mock import MagicMock, patch

from tfqa.core import capabilities
from tfqa.core.models import ToolCapability


class CoreCapabilitiesTest(TestCase):
    def test_probe_capabilities_tool_present_with_version(self) -> None:
        with patch.object(capabilities, "_cache", {}):
            with patch("shutil.which", return_value="/usr/bin/f3probe"):
                mock_proc = MagicMock()
                mock_proc.stdout = "f3probe 8.0\n"
                mock_proc.stderr = ""
                with patch("subprocess.run", return_value=mock_proc):
                    caps = capabilities.probe_capabilities(["f3probe"])

        self.assertIn("f3probe", caps.external_tools)
        tool = caps.external_tools["f3probe"]
        self.assertIsInstance(tool, ToolCapability)
        self.assertTrue(tool.available)
        self.assertEqual(tool.path, "/usr/bin/f3probe")
        self.assertIsNotNone(tool.version)

    def test_probe_capabilities_tool_missing(self) -> None:
        with patch.object(capabilities, "_cache", {}):
            with patch("shutil.which", return_value=None):
                caps = capabilities.probe_capabilities(["nonexistent"])

        tool = caps.external_tools["nonexistent"]
        self.assertFalse(tool.available)
        self.assertIsNone(tool.version)

    def test_check_tool_uses_cache(self) -> None:
        reload_module(capabilities)
        with patch("shutil.which", return_value="/usr/bin/foo") as mock_which:
            mock_proc = MagicMock()
            mock_proc.stdout = "foo 1.2\n"
            mock_proc.stderr = ""
            with patch("subprocess.run", return_value=mock_proc):
                first = capabilities.check_tool("foo")
                second = capabilities.check_tool("foo")

        self.assertEqual(mock_which.call_count, 1)
        self.assertEqual(first.name, "foo")
        self.assertTrue(first.available)
        self.assertEqual(second.available, first.available)

    def test_get_version_error_handling(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/f3probe"):
            with patch("subprocess.run", side_effect=Exception("boom")):
                caps = capabilities.probe_capabilities(["f3probe"])

        tool = caps.external_tools["f3probe"]
        self.assertTrue(tool.available)
        self.assertIsNone(tool.version)

    def test_clear_cache_permits_reprobe(self) -> None:
        with patch.object(capabilities, "_cache", {}):
            with patch("shutil.which", return_value="/usr/bin/tool") as mock_which:
                mock_proc = MagicMock()
                mock_proc.stdout = "tool 1.0\n"
                mock_proc.stderr = ""
                with patch("subprocess.run", return_value=mock_proc):
                    capabilities.probe_capabilities(["tool"])
                    clear_cache = capabilities.__dict__["_clear_cache"]
                    clear_cache()
                    capabilities.probe_capabilities(["tool"])

        self.assertEqual(mock_which.call_count, 2)
