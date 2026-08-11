"""Template: test for test engine implementations (tfqa.tests.*).

Copy and adapt when adding tests for test engines. Demonstrates using fixtures and monkeypatching I/O.
"""

import pytest
from datetime import datetime
from typing import Any, Dict


@pytest.fixture
def run_context() -> Dict[str, Any]:
    """Minimal run_context fixture; replace with real RunContext when available."""
    return {
        "run_id": "test-run-1",
        "started_at": datetime.now(),
        "device": {"path": "/dev/fake", "size_bytes": 128000000000},
        "mode": "ai",
    }


@pytest.mark.asyncio
async def test_engine_basic(run_context: Dict[str, Any], monkeypatch: Any) -> None:
    """Template test - adapt to actual engine and replace skip with assertions."""
    pytest.skip("Template test - replace with real assertions for engine")
