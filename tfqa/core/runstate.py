"""Durable state for runs that outlive a single foreground invocation.

`full-capacity-test` on a 128 GB card is hours of I/O, and `surface-scan` is
worse. Every command used to be synchronous, so a caller either blocked past
any sane timeout or killed the process mid-write. This mattered little while
the engines were stubs returning instantly; once they did real work it became
the limiting factor on driving the tool programmatically.

A run writes a small JSON file next to its JSONL log. The state is a plain file
rather than a daemon or a socket so that a crashed or killed run leaves
something readable behind, and so `tfqa status` works from any process.

Cancellation is cooperative where it can be and a signal where it cannot. A run
cancelled mid-write leaves the device partially written, which is recorded in
the state rather than left for the caller to infer.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from tfqa.core.errors import ArgumentError
from tfqa.core.logging import _default_log_dir

RunState = Literal["running", "completed", "failed", "cancelled", "orphaned"]

STATE_SUFFIX = ".state.json"

#: A run whose process is gone without a terminal state was killed or crashed.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "orphaned"})


def _proc_stat(pid: int) -> tuple[str, int] | None:
    """This process's run state and start time, or None if it is gone."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # The comm field may contain spaces and parentheses, so parse after it.
    try:
        tail = stat[stat.rindex(")") + 2 :].split()
        return tail[0], int(tail[19])  # state (3), starttime (22)
    except (ValueError, IndexError):  # pragma: no cover - unexpected format
        return None


def process_start_ticks(pid: int) -> int | None:
    """The process's start time, used to tell it from a later reuse of its pid.

    `kill(pid, 0)` only says *a* process exists. Signalling on that alone means
    a crashed run whose pid has been recycled sends SIGTERM to an unrelated
    process -- which matters most when tfqa runs as root.
    """

    stat = _proc_stat(pid)
    return stat[1] if stat else None


@dataclass
class RunStatus:
    """What a caller can learn about a run without waiting for it."""

    run_id: str
    command: str
    state: RunState = "running"
    pid: int | None = None
    pid_start: int | None = None
    device_path: str | None = None
    phase: str | None = None
    completed_bytes: int = 0
    total_bytes: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    error_code: str | None = None
    message: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    wrote_to_device: bool = False

    @property
    def percent(self) -> float | None:
        if self.total_bytes <= 0:
            return None
        return round(min(100.0, self.completed_bytes / self.total_bytes * 100), 2)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["percent"] = self.percent
        payload["running"] = self.state == "running"
        return payload


def state_dir(log_dir: Path | None = None) -> Path:
    return Path(log_dir) if log_dir else _default_log_dir()


def state_path(run_id: str, log_dir: Path | None = None) -> Path:
    return state_dir(log_dir) / f"run-{run_id}{STATE_SUFFIX}"


def write(status: RunStatus, log_dir: Path | None = None) -> Path:
    """Persist a status atomically, so a reader never sees a half-written file."""

    status.updated_at = time.time()
    target = state_path(status.run_id, log_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(status.to_dict(), indent=1), encoding="utf-8")
    temporary.replace(target)
    return target


def _is_our_process(pid: int | None, pid_start: int | None) -> bool:
    """True only when this pid is still the process we started."""

    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return pid_start is None
    stat = _proc_stat(pid)
    if stat is None:  # pragma: no cover - exited between the two checks
        return False
    state, started = stat
    if state == "Z":
        # A zombie has exited; only its exit status is still held. Treating it
        # as alive would keep a finished run reported as running.
        return False
    if pid_start is None:
        # Recorded before start times were tracked; existence is all we have.
        return True
    return started == pid_start


def read(run_id: str, log_dir: Path | None = None) -> RunStatus:
    """Load a run's status, reconciling it with whether the process still exists."""

    target = state_path(run_id, log_dir)
    if not target.is_file():
        raise ArgumentError(
            message=f"No run recorded with id {run_id!r}",
            details={"run_id": run_id, "looked_in": str(state_dir(log_dir))},
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArgumentError(
            message=f"Run state for {run_id!r} is unreadable: {exc}",
            details={"run_id": run_id, "path": str(target)},
        ) from exc

    fields = {f for f in RunStatus.__dataclass_fields__}
    try:
        status = RunStatus(**{k: v for k, v in payload.items() if k in fields})
    except TypeError as exc:
        # A syntactically valid but incomplete file would otherwise raise
        # TypeError out of the CLI, bypassing the typed error handling.
        raise ArgumentError(
            message=f"Run state for {run_id!r} is incomplete: {exc}",
            details={"run_id": run_id, "path": str(target)},
        ) from exc

    # A run marked running whose process is gone was killed or crashed. Saying
    # so beats reporting progress that will never advance.
    if status.state == "running" and not _is_our_process(status.pid, status.pid_start):
        status.state = "orphaned"
        status.message = (
            "The process is no longer running and did not record an outcome; "
            "it was killed or it crashed."
        )
    return status


def list_runs(log_dir: Path | None = None, limit: int = 50) -> list[RunStatus]:
    directory = state_dir(log_dir)
    if not directory.is_dir():
        return []
    found: list[RunStatus] = []
    for path in sorted(directory.glob(f"run-*{STATE_SUFFIX}"), reverse=True):
        run_id = path.name[len("run-") : -len(STATE_SUFFIX)]
        try:
            found.append(read(run_id, log_dir))
        except ArgumentError as exc:
            # Reported, not dropped. Silently omitting a corrupt entry hides
            # exactly the crashed run someone is trying to diagnose.
            found.append(
                RunStatus(
                    run_id=run_id,
                    command="unknown",
                    state="orphaned",
                    message=f"State file unreadable: {exc.message}",
                )
            )
        if len(found) >= limit:
            break
    return found


def cancel(
    run_id: str, log_dir: Path | None = None, grace_seconds: float = 2.0
) -> RunStatus:
    """Ask a run to stop, and record that it was asked.

    The signal is delivered to the process; a run that is mid-write will leave
    the device partially written, which `wrote_to_device` already records.
    """

    status = read(run_id, log_dir)
    if status.state in TERMINAL_STATES:
        raise ArgumentError(
            message=f"Run {run_id!r} is already {status.state}",
            details={"run_id": run_id, "state": status.state},
        )
    if not status.pid:
        raise ArgumentError(
            message=f"Run {run_id!r} has no recorded process to cancel",
            details={"run_id": run_id},
        )
    if not _is_our_process(status.pid, status.pid_start):
        # The pid may have been recycled; signalling it would hit a stranger.
        status.state = "orphaned"
        status.message = "The process had already exited; nothing was signalled."
        write(status, log_dir)
        return status

    try:
        os.kill(status.pid, signal.SIGTERM)
    except ProcessLookupError:
        status.state = "orphaned"
        status.message = "The process had already exited."
        write(status, log_dir)
        return status
    except PermissionError as exc:
        raise ArgumentError(
            message=f"Not permitted to signal the process running {run_id!r}",
            details={"run_id": run_id, "pid": status.pid, "error": str(exc)},
        ) from exc

    # SIGTERM is a request. Give the process a moment, then report what is
    # actually true rather than assuming it stopped.
    deadline = time.time() + grace_seconds
    while time.time() < deadline and _is_our_process(status.pid, status.pid_start):
        time.sleep(0.05)

    stopped = not _is_our_process(status.pid, status.pid_start)
    status.state = "cancelled" if stopped else "running"
    status.finished_at = time.time() if stopped else None
    status.message = (
        "Cancellation requested. A run interrupted mid-write leaves the device "
        "partially written; check wrote_to_device before reusing the card."
        if stopped
        else (
            "SIGTERM sent, but the process is still running. It may be "
            "finishing a write; check again shortly."
        )
    )
    write(status, log_dir)
    return status
