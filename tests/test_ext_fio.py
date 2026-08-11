import json
import unittest
from typing import Any, cast
from unittest.mock import patch

from tfqa.core.errors import RuntimeIOError, ToolNotFoundError
from tfqa.ext import fio


class _FakeProcess:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_success_which(path: str | None = None) -> str:
    return "/usr/bin/fio"


def _fake_success_run(*args: Any, **kwargs: Any) -> _FakeProcess:
    cmd: list[str]
    if args and isinstance(args[0], list):
        cmd = cast(list[str], args[0])
    else:
        cmd = []
    if "--version" in cmd:
        return _FakeProcess("fio-3.40\n")

    payload: dict[str, object] = {
        "fio version": "fio-3.40",
        "jobs": [
            {
                "jobname": "tfqa",
                "iodepth": 32,
                "runtime": 30,
                "read": {"bw": 102400, "iops": 1500},
                "write": {"bw": 51200, "iops": 800},
                "lat_ns": {"mean": 5000},
            }
        ],
    }
    return _FakeProcess(json.dumps(payload))


def _fake_missing_which(path: str | None = None) -> None:
    return None


def _bad_run(*args: Any, **kwargs: Any) -> _FakeProcess:
    return _FakeProcess("not-json")


class TestFio(unittest.TestCase):
    def test_run_fio_job_success(self) -> None:
        with (
            patch("shutil.which", side_effect=_fake_success_which),
            patch("tfqa.ext.fio.subprocess.run", side_effect=_fake_success_run),
        ):
            result = fio.run_fio_job(
                "/dev/fake",
                "tfqa",
                rw="readwrite",
                bs="4k",
                iodepth=32,
                runtime=30,
            )

        self.assertEqual(result["fio_version"], "fio-3.40")
        self.assertEqual(result["read_bw_kbps"], 102400)
        self.assertEqual(result["write_bw_kbps"], 51200)
        self.assertEqual(result["latency_ns"], 5000)

    def test_run_fio_job_tool_missing(self) -> None:
        with patch("shutil.which", side_effect=_fake_missing_which):
            with self.assertRaises(ToolNotFoundError):
                fio.run_fio_job(
                    "/dev/fake", "tfqa", rw="read", bs="1m", iodepth=32, runtime=30
                )

    def test_run_fio_job_json_errors(self) -> None:
        with (
            patch("shutil.which", side_effect=_fake_success_which),
            patch("tfqa.ext.fio.subprocess.run", side_effect=_bad_run),
        ):
            with self.assertRaises(RuntimeIOError):
                fio.run_fio_job(
                    "/dev/fake", "tfqa", rw="read", bs="1m", iodepth=32, runtime=30
                )
