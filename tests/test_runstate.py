"""Background runs: durable state, progress, orphan detection, cancellation.

Every command used to be synchronous. `full-capacity-test` on a 128 GB card is
hours of I/O, so a caller either blocked past any sane timeout or killed the
process mid-write. That mattered little while the engine was a stub returning
instantly; once it did real work it became the limiting factor on driving the
tool programmatically.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from tfqa.cli.main import _detach, app
from tfqa.core import runstate
from tfqa.core.errors import ArgumentError
from tfqa.core.models import CLIResponse, DeviceInfo

runner = CliRunner()


@pytest.fixture
def log_dir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _status(run_id: str = "r1", **kwargs: object) -> runstate.RunStatus:
    return runstate.RunStatus(
        run_id=run_id,
        command=str(kwargs.pop("command", "full-capacity-test")),
        **kwargs,  # type: ignore[arg-type]
    )


class TestStatePersistence:
    def test_a_run_round_trips(self, log_dir: Path) -> None:
        runstate.write(_status(device_path="/dev/sdz", total_bytes=100), log_dir)
        loaded = runstate.read("r1", log_dir)

        assert loaded.command == "full-capacity-test"
        assert loaded.device_path == "/dev/sdz"
        assert loaded.total_bytes == 100

    def test_progress_is_a_percentage_when_the_total_is_known(self) -> None:
        assert _status(completed_bytes=25, total_bytes=100).percent == 25.0

    def test_percent_is_absent_when_the_total_is_not_known(self) -> None:
        # Better than reporting 0% for a run whose size nobody measured.
        assert _status(completed_bytes=25).percent is None

    def test_an_unknown_run_is_an_argument_error(self, log_dir: Path) -> None:
        with pytest.raises(ArgumentError) as excinfo:
            runstate.read("nope", log_dir)
        assert excinfo.value.error_code == "INVALID_ARGUMENT"

    def test_a_corrupt_state_file_is_reported_not_swallowed(
        self, log_dir: Path
    ) -> None:
        runstate.state_path("bad", log_dir).write_text("{not json")
        with pytest.raises(ArgumentError):
            runstate.read("bad", log_dir)

    def test_writes_are_atomic(self, log_dir: Path) -> None:
        # A reader polling the file must never see it half-written.
        runstate.write(_status(), log_dir)
        assert not list(log_dir.glob("*.tmp"))

    def test_listing_is_newest_first(self, log_dir: Path) -> None:
        for run_id in ("20260101T000000Z", "20260102T000000Z"):
            runstate.write(_status(run_id), log_dir)
        assert [r.run_id for r in runstate.list_runs(log_dir)] == [
            "20260102T000000Z",
            "20260101T000000Z",
        ]

    def test_listing_an_absent_directory_is_empty(self) -> None:
        assert runstate.list_runs(Path("/nonexistent-tfqa-runs")) == []


class TestOrphanDetection:
    def test_a_dead_process_marked_running_is_orphaned(self, log_dir: Path) -> None:
        # Reporting progress that will never advance is worse than saying the
        # process is gone.
        runstate.write(_status(pid=2**22, state="running"), log_dir)
        loaded = runstate.read("r1", log_dir)

        assert loaded.state == "orphaned"
        assert "killed" in str(loaded.message)

    def test_a_live_process_stays_running(self, log_dir: Path) -> None:
        runstate.write(_status(pid=os.getpid(), state="running"), log_dir)
        assert runstate.read("r1", log_dir).state == "running"

    def test_a_completed_run_is_left_alone(self, log_dir: Path) -> None:
        runstate.write(_status(pid=2**22, state="completed"), log_dir)
        assert runstate.read("r1", log_dir).state == "completed"


class TestCancellation:
    def test_cancelling_signals_the_process(self, log_dir: Path) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            runstate.write(_status(pid=child.pid, state="running"), log_dir)
            cancelled = runstate.cancel("r1", log_dir)

            assert cancelled.state == "cancelled"
            assert child.wait(timeout=10) != 0 or child.returncode is not None
        finally:
            if child.poll() is None:  # pragma: no cover - cleanup
                child.kill()

    def test_cancelling_warns_when_the_device_was_written(self, log_dir: Path) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            runstate.write(
                _status(pid=child.pid, state="running", wrote_to_device=True), log_dir
            )
            cancelled = runstate.cancel("r1", log_dir)
            assert "partially written" in str(cancelled.message)
        finally:
            if child.poll() is None:  # pragma: no cover - cleanup
                child.kill()

    def test_cancelling_a_finished_run_is_rejected(self, log_dir: Path) -> None:
        runstate.write(_status(state="completed"), log_dir)
        with pytest.raises(ArgumentError):
            runstate.cancel("r1", log_dir)

    def test_cancelling_a_vanished_process_records_it(self, log_dir: Path) -> None:
        runstate.write(_status(pid=2**22, state="running"), log_dir)
        # read() reports it as orphaned, so cancel refuses rather than
        # signalling a pid that may have been reused.
        with pytest.raises(ArgumentError):
            runstate.cancel("r1", log_dir)


class TestDetachSpawnsCorrectly:
    def test_the_child_reruns_without_the_detach_flag(self, log_dir: Path) -> None:
        # Otherwise the child detaches again, forever.
        argv = ["full-capacity-test", "--device", "/dev/sdz", "--detach", "--force"]
        with (
            patch.object(sys, "argv", ["tfqa", *argv]),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value.pid = 4321
            pid = _detach(None, "run-1", log_dir)  # type: ignore[arg-type]

        assert pid == 4321
        command = popen.call_args.args[0]
        assert "--detach" not in command
        assert "full-capacity-test" in command

    def test_the_child_is_told_which_run_it_is(self, log_dir: Path) -> None:
        # The child must record progress under the id the parent reported, or
        # `tfqa status <id>` would find nothing.
        with (
            patch.object(sys, "argv", ["tfqa", "full-capacity-test", "--detach"]),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value.pid = 1
            _detach(None, "run-7", log_dir)  # type: ignore[arg-type]

        environment = popen.call_args.kwargs["env"]
        assert environment["TFQA_RUN_ID"] == "run-7"
        assert environment["TFQA_LOG_DIR"] == str(log_dir)

    def test_the_child_outlives_the_parent(self, log_dir: Path) -> None:
        with (
            patch.object(sys, "argv", ["tfqa", "full-capacity-test", "--detach"]),
            patch("subprocess.Popen") as popen,
        ):
            popen.return_value.pid = 1
            _detach(None, "run-1", log_dir)  # type: ignore[arg-type]

        assert popen.call_args.kwargs["start_new_session"] is True


class TestStatusCommand:
    def test_reporting_one_run(self, log_dir: Path) -> None:
        runstate.write(
            _status(pid=os.getpid(), completed_bytes=5, total_bytes=10), log_dir
        )
        result = runner.invoke(
            app, ["--log-dir", str(log_dir), "status", "r1", "--output", "json"]
        )

        assert result.exit_code == 0, result.stdout
        run = CLIResponse.model_validate_json(result.stdout).data["run"]
        assert run["state"] == "running"
        assert run["percent"] == 50.0

    def test_listing_runs(self, log_dir: Path) -> None:
        runstate.write(_status("a"), log_dir)
        runstate.write(_status("b"), log_dir)
        result = runner.invoke(
            app, ["--log-dir", str(log_dir), "status", "--output", "json"]
        )

        assert len(CLIResponse.model_validate_json(result.stdout).data["runs"]) == 2

    def test_an_unknown_run_exits_non_zero(self, log_dir: Path) -> None:
        result = runner.invoke(
            app, ["--log-dir", str(log_dir), "status", "missing", "--output", "json"]
        )
        assert result.exit_code == 2
        assert json.loads(result.stdout)["error_code"] == "INVALID_ARGUMENT"

    def test_human_output_warns_about_a_partial_write(self, log_dir: Path) -> None:
        runstate.write(
            _status(state="cancelled", wrote_to_device=True, pid=os.getpid()), log_dir
        )
        result = runner.invoke(app, ["--log-dir", str(log_dir), "status", "r1"])

        assert "wrote to the device" in result.stdout


class TestForegroundRunsAreTracked:
    def test_a_completed_run_records_its_metrics(self, log_dir: Path) -> None:
        image = log_dir / "dev.img"
        image.write_bytes(b"\0" * (256 * 1024))
        device = DeviceInfo(
            path=str(image),
            name="dev.img",
            size_bytes=256 * 1024,
            is_removable=True,
            is_system_disk=False,
            mountpoints=[],
            transport="usb",
        )
        with patch("tfqa.core.devices.get_device", return_value=device):
            result = runner.invoke(
                app,
                [
                    "--log-dir",
                    str(log_dir),
                    "--yes",
                    "full-capacity-test",
                    "--device",
                    str(image),
                    "--force",
                    "--block-size",
                    str(64 * 1024),
                    "--output",
                    "json",
                ],
            )
        assert result.exit_code == 0, result.stdout
        run_id = CLIResponse.model_validate_json(result.stdout).run_id
        assert run_id

        tracked = runstate.read(run_id, log_dir)
        assert tracked.state == "completed"
        assert tracked.percent == 100.0
        assert tracked.phase == "write-verify"
        assert tracked.wrote_to_device
        assert "coverage_percent" in tracked.metrics

    def test_a_failing_run_is_recorded_as_failed(self, log_dir: Path) -> None:
        device = DeviceInfo(
            path=str(log_dir / "missing.img"),
            name="missing.img",
            size_bytes=1024,
            is_removable=True,
            is_system_disk=False,
            mountpoints=[],
            transport="usb",
        )
        with patch("tfqa.core.devices.get_device", return_value=device):
            result = runner.invoke(
                app,
                [
                    "--log-dir",
                    str(log_dir),
                    "--yes",
                    "full-capacity-test",
                    "--device",
                    device.path,
                    "--force",
                    "--output",
                    "json",
                ],
            )
        assert result.exit_code != 0
        (tracked,) = runstate.list_runs(log_dir)
        assert tracked.state == "failed"
        # A plain OSError carries no tfqa error code; the message still says
        # what happened, which is the point of recording it at all.
        assert tracked.message
        assert tracked.finished_at
