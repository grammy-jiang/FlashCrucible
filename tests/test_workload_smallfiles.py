from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tfqa.core.models import DeviceInfo, RunContext
from tfqa.tests.workload import smallfiles


def _stub_run_context(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id="run-smallfiles",
        started_at=datetime.now(timezone.utc),
        device=DeviceInfo(
            path="/dev/fake",
            name="fake",
            size_bytes=1_000_000,
            is_removable=True,
            is_system_disk=False,
            model="FakeModel",
            vendor="FakeVendor",
        ),
        config_profile="workload-smallfiles",
        destructive=False,
        mode="ai",
        log_dir=tmp_path,
    )


class TestSmallFilesWorkload(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_smallfile_workload_reports_metrics(self) -> None:
        ctx = _stub_run_context(self.tmp_path)
        config = smallfiles.SmallFileWorkloadConfig(
            file_count=3,
            file_size_bytes=8,
            working_dir=self.tmp_path,
            delete_after=True,
            read_after_write=True,
        )

        def fake_write(path: Path, size: int) -> int:
            return size

        def fake_read(path: Path) -> int:
            return 4

        def fake_delete(path: Path) -> None:
            return None

        def fake_event(
            run_id: str, event: dict[str, Any], log_dir: Path | None
        ) -> Path:
            return self.tmp_path / "smallfiles.jsonl"

        result = smallfiles.run_small_file_workload(
            ctx,
            config,
            write_file=fake_write,
            read_file=fake_read,
            delete_file=fake_delete,
            event_emitter=fake_event,
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.metrics["files_created"], 3)
        self.assertEqual(result.metrics["files_read"], 3)
        self.assertEqual(result.metrics["files_deleted"], 3)
        self.assertEqual(result.metrics["total_bytes_written"], 24)
        self.assertEqual(result.metrics["total_bytes_read"], 12)
        self.assertEqual(result.metrics["total_errors"], 0)
        self.assertEqual(result.logs_path, self.tmp_path / "smallfiles.jsonl")
        self.assertEqual(result.details["config"]["file_count"], 3)

    def test_smallfile_workload_handles_errors(self) -> None:
        ctx = _stub_run_context(self.tmp_path)
        config = smallfiles.SmallFileWorkloadConfig(
            file_count=2,
            file_size_bytes=4,
            working_dir=self.tmp_path,
            delete_after=False,
            read_after_write=True,
        )

        def failing_write(path: Path, size: int) -> int:
            if path.name.endswith("_0002.bin"):
                raise IOError("boom")
            return size

        def fake_read(path: Path) -> int:
            return config.file_size_bytes

        def fake_event(
            run_id: str, event: dict[str, Any], log_dir: Path | None
        ) -> Path:
            return self.tmp_path / "smallfiles.jsonl"

        result = smallfiles.run_small_file_workload(
            ctx,
            config,
            write_file=failing_write,
            read_file=fake_read,
            delete_file=lambda _: None,
            event_emitter=fake_event,
        )

        self.assertEqual(result.status, "warning")
        self.assertEqual(result.metrics["total_errors"], 1)
        self.assertIn("Some file operations failed", result.warnings[0])
