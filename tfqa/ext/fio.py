"""Wrapper utilities for invoking fio benchmarks."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, List

from tfqa.core.errors import RuntimeIOError, TimeoutError, ToolNotFoundError

_FIO_CMD = "fio"


def _find_tool() -> str:
    path = shutil.which(_FIO_CMD)
    if not path:
        raise ToolNotFoundError(_FIO_CMD)
    return path


def _probe_version(executable: str) -> str | None:
    try:
        proc = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = (proc.stdout or proc.stderr or "").strip().splitlines()
        return output[0] if output else None
    except subprocess.TimeoutExpired:
        return None
    except subprocess.CalledProcessError:
        return None


def _build_command(
    executable: str,
    device_path: str,
    job_name: str,
    rw: str,
    bs: str,
    iodepth: int,
    runtime: float,
    block_size: str,
    extra_args: List[str] | None = None,
) -> List[str]:
    cmd: List[str] = [
        executable,
        "--name",
        job_name,
        "--filename",
        device_path,
        "--direct",
        "1",
        "--bs",
        bs,
        "--iodepth",
        str(iodepth),
        "--rw",
        rw,
        "--time_based",
        "1",
        "--runtime",
        str(runtime),
        "--output-format=json",
        "--group_reporting",
        "1",
        "--numjobs",
        "1",
    ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def _parse_job(job: dict[str, Any]) -> dict[str, Any]:
    read_stats = job.get("read", {})
    write_stats = job.get("write", {})
    latency_ns = job.get("lat_ns", {}).get("mean")
    return {
        "read_bw_kbps": read_stats.get("bw", 0),
        "write_bw_kbps": write_stats.get("bw", 0),
        "read_iops": read_stats.get("iops", 0),
        "write_iops": write_stats.get("iops", 0),
        "latency_ns": latency_ns,
        "runtime": job.get("runtime", 0),
        "iodepth": job.get("iodepth", 0),
    }


def run_fio_job(
    device_path: str,
    job_name: str,
    *,
    rw: str,
    bs: str,
    iodepth: int,
    runtime: float,
    extra_args: List[str] | None = None,
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    executable = _find_tool()
    version = _probe_version(executable)
    cmd = _build_command(
        executable,
        device_path,
        job_name,
        rw,
        bs,
        iodepth,
        runtime,
        bs,
        extra_args=extra_args,
    )
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            "fio timed out",
            timeout_seconds,
            {"device_path": device_path, "job": job_name},
        ) from exc

    if proc.returncode != 0:
        raise RuntimeIOError(
            "fio reported an error",
            {
                "device_path": device_path,
                "job": job_name,
                "exit_code": proc.returncode,
                "stderr": (proc.stderr or "").strip(),
            },
        )

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeIOError(
            "fio produced invalid JSON",
            {
                "device_path": device_path,
                "job": job_name,
                "stdout": (proc.stdout or "").strip(),
            },
        ) from exc

    jobs = payload.get("jobs")
    if not jobs:
        raise RuntimeIOError(
            "fio missing job results",
            {"job": job_name, "device_path": device_path},
        )

    job_stats = _parse_job(jobs[0])
    return {
        "job_name": job_name,
        "fio_version": version,
        "read_bw_kbps": job_stats["read_bw_kbps"],
        "write_bw_kbps": job_stats["write_bw_kbps"],
        "read_iops": job_stats["read_iops"],
        "write_iops": job_stats["write_iops"],
        "latency_ns": job_stats["latency_ns"],
        "runtime": job_stats["runtime"],
        "iodepth": job_stats["iodepth"],
        "raw_jobs": jobs,
    }
