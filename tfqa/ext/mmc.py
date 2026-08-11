from __future__ import annotations

from typing import TypedDict


class MmcCid(TypedDict, total=False):
    manufacturer_id: int
    product_name: str
    product_revision: str
    serial_number: str
    manufacture_date: str


class MmcHealth(TypedDict, total=False):
    life_used_percent: int
    power_on_count: int
    read_error_count: int
    write_error_count: int
    temperature_celsius: int


__all__ = ["MmcCid", "MmcHealth", "read_cid", "read_health"]


def read_cid(device_path: str) -> MmcCid:
    """Return a mock CID record for the provided device."""

    identifier = abs(hash(device_path)) & 0xFFFFFFFF
    return MmcCid(
        manufacturer_id=0x1234,
        product_name="FlashCrucibleMock",
        product_revision="1.1",
        serial_number=f"0x{identifier:08X}",
        manufacture_date="2025-01-20",
    )


def read_health(device_path: str) -> MmcHealth:
    """Return synthetic health metrics for the provided device."""

    base_value = abs(hash(device_path)) % 100
    return MmcHealth(
        life_used_percent=min(5 + base_value // 20, 15),
        power_on_count=32 + base_value,
        read_error_count=0,
        write_error_count=0,
        temperature_celsius=32 + (base_value % 5),
    )
