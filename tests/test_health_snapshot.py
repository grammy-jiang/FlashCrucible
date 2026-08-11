"""Tests for the health snapshot aggregator.

The snapshot used to fill every field from `hash(device_path)`, so the same
card reported a different serial number and wear figure on each run and the
result claimed `source: "mmc-utils+sdmon"` regardless of what had been read.
These tests pin the replacement contract: report what answered, name what did
not, and never invent a value.
"""

import unittest
from unittest.mock import patch

from tfqa.core.errors import RuntimeIOError, ToolNotFoundError
from tfqa.core.models import DeviceInfo
from tfqa.tests.health.snapshot import run_health_snapshot


def make_device(path: str = "/dev/sdz", name: str = "sdX") -> DeviceInfo:
    return DeviceInfo(
        path=path,
        name=name,
        model="Model",
        vendor="Vendor",
        serial="SN",
        size_bytes=16 * 1024,
        is_removable=True,
        is_system_disk=False,
        mountpoints=[],
        transport="usb",
    )


CARD_CID = {
    "product_name": "SD32G",
    "serial_number": "0x0abc1234",
    "is_card_identity": True,
    "source": "sysfs:mmc",
}
READER_CID = {
    "product_name": "Generic- USB3.0 CRW",
    "is_card_identity": False,
    "source": "sysfs:scsi",
}
EXTCSD_HEALTH = {"life_used_percent": 20, "pre_eol_state": "normal", "source": "mmc"}
SDMON_HEALTH = {"life_used_percent": 30, "power_on_count": 12, "source": "sdmon"}


def _patches(cid=None, extcsd=None, sdmon_health=None):
    """Patch each reader: None means it fails with its usual error."""

    def _make(target, value, default_error):
        if value is None:
            return patch(target, side_effect=default_error)
        if isinstance(value, Exception):
            return patch(target, side_effect=value)
        return patch(target, return_value=value)

    return (
        _make("tfqa.ext.mmc.read_cid", cid, RuntimeIOError("no identity", {})),
        _make("tfqa.ext.mmc.read_health", extcsd, RuntimeIOError("no extcsd", {})),
        _make("tfqa.ext.sdmon.read_health", sdmon_health, ToolNotFoundError("sdmon")),
    )


class HealthSnapshotTest(unittest.TestCase):
    def _run(self, **kwargs):
        device = make_device()
        cid_p, extcsd_p, sdmon_p = _patches(**kwargs)
        with cid_p, extcsd_p, sdmon_p:
            return run_health_snapshot(device)

    def test_nothing_available_reports_no_data(self):
        snapshot = self._run()

        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["health"], {})
        self.assertEqual(snapshot["cid"], {})
        self.assertEqual(snapshot["source"], "none")

    def test_unavailable_sources_carry_a_reason(self):
        snapshot = self._run()

        for name in ("sysfs", "mmc-extcsd", "sdmon"):
            with self.subTest(source=name):
                status = snapshot["sources"][name]
                self.assertFalse(status["available"])
                self.assertTrue(status["reason"])
                self.assertTrue(status["error_code"])

    def test_missing_sdmon_is_reported_as_a_missing_tool(self):
        snapshot = self._run()

        self.assertEqual(snapshot["sources"]["sdmon"]["error_code"], "EXT_TOOL_MISSING")

    def test_extcsd_only(self):
        snapshot = self._run(cid=dict(CARD_CID), extcsd=dict(EXTCSD_HEALTH))

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["health"]["life_used_percent"], 20)
        self.assertEqual(snapshot["source"], "sysfs:mmc+mmc-utils")
        self.assertNotIn("source", snapshot["health"])
        self.assertFalse(snapshot["sources"]["sdmon"]["available"])

    def test_sdmon_refines_the_extcsd_estimate(self):
        # sdmon reads vendor registers directly, so it wins where both answer.
        snapshot = self._run(
            cid=dict(CARD_CID),
            extcsd=dict(EXTCSD_HEALTH),
            sdmon_health=dict(SDMON_HEALTH),
        )

        self.assertEqual(snapshot["health"]["life_used_percent"], 30)
        self.assertEqual(snapshot["health"]["power_on_count"], 12)
        self.assertEqual(snapshot["source"], "sysfs:mmc+mmc-utils+sdmon")

    def test_sdmon_raw_payload_is_kept_in_details(self):
        payload = dict(SDMON_HEALTH)
        payload["raw"] = {"healthStatusPercentUsed": 30}
        payload["sdmon_version"] = "sdmon 1.1"
        snapshot = self._run(sdmon_health=payload)

        self.assertEqual(
            snapshot["details"]["sdmon_raw"], {"healthStatusPercentUsed": 30}
        )
        self.assertEqual(snapshot["details"]["sdmon_version"], "sdmon 1.1")
        # Bookkeeping keys do not leak into the metrics.
        self.assertNotIn("raw", snapshot["health"])
        self.assertNotIn("sdmon_version", snapshot["health"])

    def test_card_identity_is_not_flagged_as_a_reader(self):
        snapshot = self._run(cid=dict(CARD_CID))

        self.assertTrue(snapshot["cid"]["is_card_identity"])
        self.assertNotIn("identity_is_not_card_cid", snapshot["details"])

    def test_reader_identity_is_flagged(self):
        # A USB reader's model must not be recorded as the card's identity.
        snapshot = self._run(cid=dict(READER_CID))

        self.assertFalse(snapshot["cid"]["is_card_identity"])
        self.assertTrue(snapshot["details"]["identity_is_not_card_cid"])

    def test_identity_alone_is_not_health_data(self):
        snapshot = self._run(cid=dict(CARD_CID))

        self.assertFalse(snapshot["available"])
        self.assertEqual(snapshot["health"], {})

    def test_repeated_runs_agree(self):
        # The old implementation returned different numbers every call because
        # it derived them from a per-process-randomised hash.
        first = self._run(cid=dict(CARD_CID), extcsd=dict(EXTCSD_HEALTH))
        second = self._run(cid=dict(CARD_CID), extcsd=dict(EXTCSD_HEALTH))

        self.assertEqual(first, second)

    def test_device_path_is_recorded(self):
        snapshot = self._run()

        self.assertEqual(snapshot["details"]["device_path"], "/dev/sdz")
