import pytest
from datetime import datetime
from typing import Any, Dict


# Simple fixtures to be used by tests/templates. Replace with real models when available.


@pytest.fixture
def device_info() -> Dict[str, Any]:
    return {
        "path": "/dev/fake",
        "name": "fake",
        "size_bytes": 128_000_000_000,
        "is_removable": True,
        "is_system_disk": False,
        "mountpoints": [],
    }


@pytest.fixture
def run_context(device_info: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": "test-run-123",
        "started_at": datetime.now(),
        "device": device_info,
        "mode": "ai",
    }
