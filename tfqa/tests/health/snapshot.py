"""Collect a device health snapshot from whatever real sources answer.

The snapshot reports what each source actually returned and why the others did
not. It never fills a gap with a plausible number: an absent reading shows up
as `available: false` with a reason, so nothing downstream -- history, trends,
a pipeline stage record -- can mistake a guess for a measurement.
"""

from __future__ import annotations

from typing import TypedDict

from tfqa.core.errors import TFQAError
from tfqa.core.models import DeviceInfo
from tfqa.ext import mmc, sdmon


class CIDInfo(TypedDict, total=False):
    manufacturer_id: int
    oem_id: int
    product_name: str
    product_revision: str
    serial_number: str
    manufacture_date: str
    cid_register: str
    is_card_identity: bool
    source: str


class HealthMetrics(TypedDict, total=False):
    life_used_percent: int
    life_time_est_typ_a: int
    life_time_est_typ_b: int
    life_time_exceeded: bool
    pre_eol_info: int
    pre_eol_state: str
    power_on_count: int
    read_error_count: int
    write_error_count: int
    temperature_celsius: int
    manufacture_date: str


class SourceStatus(TypedDict, total=False):
    available: bool
    error_code: str
    reason: str


class HealthSnapshot(TypedDict):
    source: str
    available: bool
    cid: CIDInfo
    health: HealthMetrics
    sources: dict[str, SourceStatus]
    details: dict[str, object]


def _attempt(func, *args, **kwargs):  # type: ignore[no-untyped-def]
    """Run a reader and return (value, status) instead of raising."""

    try:
        return func(*args, **kwargs), SourceStatus(available=True)
    except TFQAError as exc:
        return None, SourceStatus(
            available=False, error_code=exc.error_code, reason=exc.message
        )


def run_health_snapshot(device: DeviceInfo) -> HealthSnapshot:
    """Collect identity and wear data from every source that can answer."""

    sources: dict[str, SourceStatus] = {}
    details: dict[str, object] = {"device_path": device.path}

    cid_value, sources["sysfs"] = _attempt(mmc.read_cid, device.path)
    cid: CIDInfo = CIDInfo(**cid_value) if cid_value else CIDInfo()

    health: HealthMetrics = HealthMetrics()
    contributors: list[str] = []

    extcsd_value, sources["mmc-extcsd"] = _attempt(mmc.read_health, device.path)
    if extcsd_value:
        extcsd = dict(extcsd_value)
        extcsd.pop("source", None)
        health.update(extcsd)  # type: ignore[typeddict-item]
        contributors.append("mmc-utils")

    sdmon_value, sources["sdmon"] = _attempt(sdmon.read_health, device.path)
    if sdmon_value:
        sdmon_data = dict(sdmon_value)
        sdmon_data.pop("source", None)
        details["sdmon_raw"] = sdmon_data.pop("raw", {})
        version = sdmon_data.pop("sdmon_version", None)
        if version:
            details["sdmon_version"] = version
        health.update(sdmon_data)  # type: ignore[typeddict-item]
        contributors.append("sdmon")

    if cid:
        contributors.insert(0, str(cid.get("source", "sysfs")))
        if cid.get("is_card_identity") is False:
            # The reader answered, not the card. Say so rather than let a
            # reader model be recorded as the card's identity.
            details["identity_is_reader_not_card"] = True

    return HealthSnapshot(
        source="+".join(contributors) if contributors else "none",
        available=bool(health),
        cid=cid,
        health=health,
        sources=sources,
        details=details,
    )
