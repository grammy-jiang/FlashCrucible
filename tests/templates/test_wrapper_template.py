"""Template: test for external tool wrappers.

Copy this file when adding tests for `tfqa.ext.*` wrappers. It demonstrates subprocess mocking
and tool-not-found behavior.
"""

import pytest
from typing import Any
from unittest.mock import patch, MagicMock


def test_wrapper_happy_path(monkeypatch: Any) -> None:
    """Template test: mock subprocess and skip (copy/ adapt for real tests)."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        pytest.skip("Template test - replace with real assertions")


def test_wrapper_tool_missing(monkeypatch: Any) -> None:
    """Template test: simulate tool-missing and skip (adapt for real tests)."""
    with patch("shutil.which", return_value=None):
        pytest.skip("Template test - replace with real assertions")
