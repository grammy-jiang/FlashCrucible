from __future__ import annotations

from typing import TypedDict

from tfqa.core.errors import RuntimeIOError, ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.ext import mmc, sdmon


class CIDInfo(TypedDict, total=False):
    manufacturer_id: int
    product_name: str
    product_revision: str
    serial_number: str
    manufacture_date: str


class HealthMetrics(TypedDict, total=False):
    life_used_percent: int
    power_on_count: int
    read_error_count: int
    write_error_count: int
    temperature_celsius: int


class HealthSnapshot(TypedDict):
    source: str
    cid: CIDInfo
    health: HealthMetrics
    details: dict[str, object]


def run_health_snapshot(device: DeviceInfo) -> HealthSnapshot:
    """Collect a health snapshot combining MMC values with sdmon when available."""

    cid = mmc.read_cid(device.path)
    health = mmc.read_health(device.path)
    details: dict[str, object] = {
        "device_path": device.path,
        "cid_provider": "mmc",
        "sdmon_available": False,
    }
    source = "tfqa.ext.mmc"

    try:
        sdmon_health = sdmon.read_health(device.path)
        health.update(sdmon_health)
        details["sdmon_available"] = True
        details["sdmon_health"] = sdmon_health
        source = "mmc-utils+sdmon"
    except ToolNotFoundError:
        pass
    except RuntimeIOError as exc:
        details["sdmon_error"] = str(exc)

    return HealthSnapshot(
        source=source,
        cid=cid,
        health=health,
        details=details,
    )
