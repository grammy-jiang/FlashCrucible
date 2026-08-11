"""Tests for the MMC identity and wear readers.

Both functions used to return numbers derived from `hash(device_path)`. Python
randomises string hashing per process, so the same card reported a different
serial number and wear figure on every run, and those values were recorded as
measurements. Nothing here may invent a value.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from tfqa.core.errors import RuntimeIOError, ToolNotFoundError
from tfqa.ext import mmc

# Trimmed from real `mmc extcsd read` output.
EXTCSD_OUTPUT = """
Boot Bus Conditions [BOOT_BUS_CONDITIONS: 0x00]
eMMC Life Time Estimation A [EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A]: 0x03
eMMC Life Time Estimation B [EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B]: 0x01
eMMC Pre EOL information [EXT_CSD_PRE_EOL_INFO]: 0x02
"""

EXTCSD_FAILURE = "ioctl: Invalid argument\nCould not read EXT_CSD from /dev/sdc\n"


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["mmc"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class ParseExtcsd(TestCase):
    def test_extracts_register_values(self):
        fields = mmc.parse_extcsd(EXTCSD_OUTPUT)

        self.assertEqual(fields["EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A"], 0x03)
        self.assertEqual(fields["EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B"], 0x01)
        self.assertEqual(fields["EXT_CSD_PRE_EOL_INFO"], 0x02)

    def test_ignores_unrelated_lines(self):
        self.assertNotIn("BOOT_BUS_CONDITIONS", mmc.parse_extcsd(EXTCSD_OUTPUT))

    def test_empty_output_yields_nothing(self):
        self.assertEqual(mmc.parse_extcsd(""), {})


class LifeTimeConversion(TestCase):
    def test_bands_map_to_their_upper_bound(self):
        # The register reports 10% bands, not a precise figure.
        self.assertEqual(mmc.life_time_to_percent(0x01), 10)
        self.assertEqual(mmc.life_time_to_percent(0x05), 50)
        self.assertEqual(mmc.life_time_to_percent(0x0A), 100)

    def test_exceeded_saturates_at_100(self):
        self.assertEqual(mmc.life_time_to_percent(mmc.LIFE_TIME_EXCEEDED), 100)

    def test_not_defined_is_none(self):
        self.assertIsNone(mmc.life_time_to_percent(0))


class ReadHealth(TestCase):
    def test_reports_the_worst_of_the_two_estimates(self):
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch("subprocess.run", return_value=_completed(EXTCSD_OUTPUT)),
        ):
            health = mmc.read_health("/dev/mmcblk0")

        self.assertEqual(health["life_time_est_typ_a"], 0x03)
        self.assertEqual(health["life_time_est_typ_b"], 0x01)
        self.assertEqual(health["life_used_percent"], 30)
        self.assertFalse(health["life_time_exceeded"])
        self.assertEqual(health["pre_eol_state"], "warning")
        self.assertEqual(health["source"], "mmc-utils:extcsd")

    def test_exceeded_estimate_is_flagged(self):
        output = "[EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A]: 0x0B\n"
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch("subprocess.run", return_value=_completed(output)),
        ):
            health = mmc.read_health("/dev/mmcblk0")

        self.assertTrue(health["life_time_exceeded"])
        self.assertEqual(health["life_used_percent"], 100)

    def test_failed_ioctl_raises_even_though_mmc_exits_zero(self):
        # mmc-utils returns 0 on a failed ioctl, so the exit code cannot be
        # trusted; the output has to be inspected.
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch(
                "subprocess.run",
                return_value=_completed(stderr=EXTCSD_FAILURE, returncode=0),
            ),
            pytest.raises(RuntimeIOError),
        ):
            mmc.read_health("/dev/sdc")

    def test_missing_tool_raises(self):
        with patch("shutil.which", return_value=None), pytest.raises(ToolNotFoundError):
            mmc.read_health("/dev/mmcblk0")

    def test_undefined_estimates_raise_instead_of_claiming_health(self):
        # 0x00 is the spec's "not defined". Recording life_time_exceeded=False
        # for it made the snapshot report health as available with no reading.
        output = (
            "[EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A]: 0x00\n"
            "[EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B]: 0x00\n"
            "[EXT_CSD_PRE_EOL_INFO]: 0x00\n"
        )
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch("subprocess.run", return_value=_completed(output)),
        ):
            with pytest.raises(RuntimeIOError) as excinfo:
                mmc.read_health("/dev/mmcblk0")

        self.assertIn("no wear estimate", excinfo.value.message)

    def test_one_defined_estimate_is_enough(self):
        output = (
            "[EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_A]: 0x00\n"
            "[EXT_CSD_DEVICE_LIFE_TIME_EST_TYP_B]: 0x02\n"
        )
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch("subprocess.run", return_value=_completed(output)),
        ):
            health = mmc.read_health("/dev/mmcblk0")

        self.assertEqual(health["life_used_percent"], 20)

    def test_pre_eol_alone_is_wear_data(self):
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch(
                "subprocess.run",
                return_value=_completed("[EXT_CSD_PRE_EOL_INFO]: 0x03\n"),
            ),
        ):
            health = mmc.read_health("/dev/mmcblk0")

        self.assertEqual(health["pre_eol_state"], "urgent")
        self.assertNotIn("life_used_percent", health)

    def test_output_without_wear_fields_raises(self):
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch(
                "subprocess.run",
                return_value=_completed("[EXT_CSD_BOOT_SIZE_MULTI]: 0x20\n"),
            ),
            pytest.raises(RuntimeIOError),
        ):
            mmc.read_health("/dev/mmcblk0")

    def test_nothing_is_invented_when_the_device_cannot_answer(self):
        with (
            patch("shutil.which", return_value="/usr/bin/mmc"),
            patch("subprocess.run", return_value=_completed(stderr=EXTCSD_FAILURE)),
        ):
            with pytest.raises(RuntimeIOError) as excinfo:
                mmc.read_health("/dev/sdc")

        self.assertIn("device_path", excinfo.value.details)
        self.assertIn("hint", excinfo.value.details)


class ReadCid(TestCase):
    def _sysfs(self, tmp_path: Path, **attrs: str) -> Path:
        directory = tmp_path / "sdz" / "device"
        directory.mkdir(parents=True)
        for name, value in attrs.items():
            (directory / name).write_text(value)
        return tmp_path

    def test_reads_the_real_cid_from_an_mmc_host(self):
        with pytest.MonkeyPatch.context() as monkeypatch:
            import tempfile

            root = Path(tempfile.mkdtemp())
            self._sysfs(
                root,
                cid="035344534433324780ab1234",
                name="SD32G",
                manfid="0x000003",
                oemid="0x5344",
                serial="0x0abc1234",
                date="04/2021",
                fwrev="0x0",
            )
            monkeypatch.setattr(mmc, "SYSFS_BLOCK", root)
            cid = mmc.read_cid("/dev/sdz")

        self.assertTrue(cid["is_card_identity"])
        self.assertEqual(cid["product_name"], "SD32G")
        self.assertEqual(cid["serial_number"], "0x0abc1234")
        self.assertEqual(cid["manufacturer_id"], 3)
        self.assertEqual(cid["source"], "sysfs:mmc")

    def test_usb_reader_identity_is_flagged_as_not_the_card(self):
        with pytest.MonkeyPatch.context() as monkeypatch:
            import tempfile

            root = Path(tempfile.mkdtemp())
            self._sysfs(root, vendor="Generic-", model="USB3.0 CRW", rev="1.00")
            monkeypatch.setattr(mmc, "SYSFS_BLOCK", root)
            cid = mmc.read_cid("/dev/sdz")

        self.assertFalse(cid["is_card_identity"])
        self.assertEqual(cid["product_name"], "Generic- USB3.0 CRW")
        self.assertEqual(cid["source"], "sysfs:scsi")
        # No CID register was read, so none is reported.
        self.assertNotIn("cid_register", cid)
        self.assertNotIn("serial_number", cid)

    def test_no_identity_available_raises(self):
        with pytest.MonkeyPatch.context() as monkeypatch:
            import tempfile

            root = Path(tempfile.mkdtemp())
            self._sysfs(root)
            monkeypatch.setattr(mmc, "SYSFS_BLOCK", root)
            with pytest.raises(RuntimeIOError):
                mmc.read_cid("/dev/sdz")

    def test_unknown_device_raises(self):
        with pytest.MonkeyPatch.context() as monkeypatch:
            import tempfile

            monkeypatch.setattr(mmc, "SYSFS_BLOCK", Path(tempfile.mkdtemp()))
            with pytest.raises(RuntimeIOError):
                mmc.read_cid("/dev/nope")

    def test_repeated_reads_agree(self):
        # The old implementation produced a different serial every call.
        with pytest.MonkeyPatch.context() as monkeypatch:
            import tempfile

            root = Path(tempfile.mkdtemp())
            self._sysfs(root, vendor="Generic-", model="USB3.0 CRW")
            monkeypatch.setattr(mmc, "SYSFS_BLOCK", root)
            self.assertEqual(mmc.read_cid("/dev/sdz"), mmc.read_cid("/dev/sdz"))
