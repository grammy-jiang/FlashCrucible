"""Tests for the sdmon wrapper.

sdmon was invoked for real, its exit code checked, and its output then thrown
away in favour of numbers derived from `hash(device_path)`. These tests pin the
replacement: parse what sdmon printed, and raise when there is nothing to parse.
"""

from __future__ import annotations

import subprocess
from unittest import TestCase
from unittest.mock import patch

import pytest

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError
from tfqa.ext import sdmon

SDMON_JSON = """{
  "smartStatus": "found",
  "manufactureYM": "2021/3",
  "healthStatusPercentUsed": 7,
  "powerOnTimes": 412,
  "temperature": 39
}"""


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["sdmon"], returncode=returncode, stdout=stdout, stderr=""
    )


class ParseOutput(TestCase):
    def test_parses_json(self):
        raw = sdmon.parse_output(SDMON_JSON)
        self.assertEqual(raw["healthStatusPercentUsed"], 7)

    def test_empty_output_is_rejected(self):
        with pytest.raises(ValueError):
            sdmon.parse_output("   ")

    def test_non_object_json_is_rejected(self):
        with pytest.raises(ValueError):
            sdmon.parse_output("[1, 2, 3]")


class MapFields(TestCase):
    def test_maps_vendor_keys_onto_shared_names(self):
        mapped = sdmon.map_fields(
            {"healthStatusPercentUsed": 7, "powerOnTimes": 412, "temperature": 39}
        )

        self.assertEqual(mapped["life_used_percent"], 7)
        self.assertEqual(mapped["power_on_count"], 412)
        self.assertEqual(mapped["temperature_celsius"], 39)

    def test_accepts_alternative_spellings(self):
        # Key names differ across vendor generations.
        mapped = sdmon.map_fields({"percentLifetimeUsed": 12})
        self.assertEqual(mapped["life_used_percent"], 12)

    def test_coerces_string_numbers(self):
        self.assertEqual(sdmon.map_fields({"powerOnTimes": "42"})["power_on_count"], 42)
        self.assertEqual(
            sdmon.map_fields({"healthStatusPercentUsed": "7%"})["life_used_percent"], 7
        )

    def test_manufacture_date_stays_a_string(self):
        self.assertEqual(
            sdmon.map_fields({"manufactureYM": "2021/3"})["manufacture_date"], "2021/3"
        )

    def test_unrecognised_payload_maps_to_nothing(self):
        self.assertEqual(sdmon.map_fields({"somethingElse": 1}), {})

    def test_booleans_are_not_treated_as_numbers(self):
        self.assertEqual(sdmon.map_fields({"powerOnTimes": True}), {})


class ReadHealth(TestCase):
    def _run(self, stdout: str = SDMON_JSON):
        with (
            patch("shutil.which", return_value="/usr/bin/sdmon"),
            patch.object(sdmon, "_probe_version", return_value="sdmon 1.1"),
            patch("subprocess.run", return_value=_completed(stdout)),
        ):
            return sdmon.read_health("/dev/mmcblk0")

    def test_returns_the_values_sdmon_printed(self):
        health = self._run()

        self.assertEqual(health["life_used_percent"], 7)
        self.assertEqual(health["power_on_count"], 412)
        self.assertEqual(health["temperature_celsius"], 39)
        self.assertEqual(health["manufacture_date"], "2021/3")
        self.assertEqual(health["source"], "sdmon")
        self.assertEqual(health["sdmon_version"], "sdmon 1.1")

    def test_keeps_the_raw_payload(self):
        # Vendor fields we do not map are still available to the caller.
        self.assertEqual(self._run()["raw"]["smartStatus"], "found")

    def test_repeated_reads_agree(self):
        self.assertEqual(self._run(), self._run())

    def test_unparseable_output_raises(self):
        with pytest.raises(RuntimeIOError) as excinfo:
            self._run("not json at all")
        self.assertIn("stdout", excinfo.value.details)

    def test_empty_output_raises(self):
        with pytest.raises(RuntimeIOError):
            self._run("")

    def test_json_without_known_fields_raises(self):
        # Better to say the card answered with nothing usable than to invent it.
        with pytest.raises(RuntimeIOError) as excinfo:
            self._run('{"somethingElse": 1}')
        self.assertIn("keys", excinfo.value.details)

    def test_missing_tool_raises(self):
        with patch("shutil.which", return_value=None), pytest.raises(ToolNotFoundError):
            sdmon.read_health("/dev/mmcblk0")

    def test_non_zero_exit_raises(self):
        error = subprocess.CalledProcessError(2, ["sdmon"], stderr="nope")
        with (
            patch("shutil.which", return_value="/usr/bin/sdmon"),
            patch.object(sdmon, "_probe_version", return_value=None),
            patch("subprocess.run", side_effect=error),
            pytest.raises(RuntimeIOError),
        ):
            sdmon.read_health("/dev/mmcblk0")

    def test_timeout_raises(self):
        with (
            patch("shutil.which", return_value="/usr/bin/sdmon"),
            patch.object(sdmon, "_probe_version", return_value=None),
            patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(["sdmon"], 1)
            ),
            pytest.raises(TimeoutError),
        ):
            sdmon.read_health("/dev/mmcblk0")
