"""Typer-based CLI entrypoint for tfqa (FlashCrucible)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from typing import Any, Callable, Literal, Sequence, cast

import click
import typer
from typer.main import get_command
from jsonschema import Draft7Validator, SchemaError
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from rich.console import Console
from rich.panel import Panel

from tfqa.core import capabilities as capabilities_mod
from tfqa.core import config as cfg_mod
from tfqa.core import devices as devices_mod
from tfqa.core import logging as logging_mod
from tfqa.core import paths
from tfqa.core import runstate
from tfqa.core import safety as safety_mod
from tfqa.core.errors import ArgumentError, TFQAError, get_exit_code
from tfqa.core.models import (
    CLIResponse,
    ConfigModel,
    DeviceInfo,
    EnduranceConfig,
    RunContext,
    TestResult,
    TestStatus,
)
from tfqa.orchestration import pipeline as pipeline_mod
from tfqa.orchestration import profile as profile_mod
from tfqa.orchestration import workflows as workflows_mod
from tfqa.reporting import history as history_mod
from tfqa.reporting import summary as summary_mod
from tfqa.reporting import trends as trends_mod
from tfqa.ext.fsck import run_fsck
from tfqa.ext.image import run_image_flash
from tfqa.tests.capacity import full as full_capacity
from tfqa.tests.capacity import quick as quick_capacity
from tfqa.tests.endurance import simple as endurance_simple
from tfqa.tests.health import snapshot as health_snapshot
from tfqa.tests.performance import basic as perf_basic
from tfqa.tests.performance import random as perf_random
from tfqa.tests.surface import scan as surface_scan_mod
from tfqa.tests.workload import smallfiles as workload_smallfiles

APP_NAME = "tfqa"
CONFIG_SHOW_COMMAND_NAME = "config show"
CONFIG_VALIDATE_COMMAND_NAME = "config validate"
DEVICE_PATH_HELP = "Block device path (e.g., /dev/sdb)."
OUTPUT_HELP = "Output format (human/json)."
DETECT_OUTPUT_HELP = OUTPUT_HELP
DRY_RUN_FLAG = "--dry-run"
FORCE_HELP = "Override the mounted/system-disk safety refusal (requires --yes)."
METRICS_LABEL = "Metrics"
DEFAULT_IMAGE_CONV_FLAGS: tuple[str, ...] = ("fsync",)
DEFAULT_IMAGE_BLOCK_SIZE = "4M"
DEFAULT_IMAGE_VERIFY = True
DEFAULT_IMAGE_WRITE_TIMEOUT = 600.0
DEFAULT_IMAGE_VERIFY_TIMEOUT = 300.0
NO_DESCRIPTION = "No description"

app = typer.Typer(name=APP_NAME, help="FlashCrucible CLI for storage QA.")
config_app = typer.Typer(name="config", help="Inspect or validate config.")
app.add_typer(config_app, name="config", help="Configuration helpers.")

# Which external tools each command needs, and what happens when one is absent.
# `capabilities` reports which tools this host has; without this an agent had to
# know independently that a missing fio affects `performance`.
_TOOL_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "quick-test": {
        "required_tools": ["f3probe"],
        "degradation": "Fails with EXT_TOOL_MISSING; there is no fallback.",
    },
    "performance": {
        "required_tools": ["fio"],
        "degradation": "Fails with EXT_TOOL_MISSING rather than estimating throughput.",
    },
    "surface-scan": {
        "required_tools": ["badblocks"],
        "degradation": "Fails with EXT_TOOL_MISSING rather than estimating coverage.",
    },
    "filesystem-check": {
        "required_tools": ["fsck"],
        "degradation": "Fails with EXT_TOOL_MISSING.",
    },
    "image-flash": {
        "required_tools": ["dd"],
        "optional_tools": ["cmp"],
        "degradation": (
            "Without cmp, fails with EXT_TOOL_MISSING before writing anything; "
            "pass --no-verify to flash without verification."
        ),
    },
    "health": {
        "optional_tools": ["mmc", "sdmon"],
        "degradation": (
            "Reports available: false with a per-source reason; identity still "
            "comes from sysfs."
        ),
    },
    "endurance": {
        "degradation": "Always fails with NOT_IMPLEMENTED; the engine does no device I/O.",
    },
    "full-capacity-test": {
        "degradation": "Native implementation; needs write access to the block device.",
    },
    "status": {
        "degradation": "Reads run state files; needs no external tool.",
    },
    "cancel": {
        "degradation": (
            "Signals the recorded process. A run stopped mid-write leaves the "
            "device partially written; check `wrote_to_device`."
        ),
    },
    "pipeline": {
        "degradation": (
            "A stage whose tool is missing is recorded as skipped; the rest of "
            "the plan still runs."
        ),
    },
}

# `describe` is what an agent reads before deciding whether something is safe
# to run, so anything that can write to the device declares itself destructive
# even when that depends on a flag -- `destructive_when` says on what.
# quick-test, surface-scan, filesystem-check, performance, and pipeline all
# reported destructive=False while calling the safety guard.
_DESCRIBE_OVERRIDES: dict[str, dict[str, Any]] = {
    "automation-report": {"destructive": False, "requires_root": False},
    CONFIG_SHOW_COMMAND_NAME: {"destructive": False, "requires_root": False},
    CONFIG_VALIDATE_COMMAND_NAME: {"destructive": False, "requires_root": False},
    "capabilities": {"destructive": False, "requires_root": False},
    "combos": {"destructive": False, "requires_root": False},
    "describe": {"destructive": False, "requires_root": False},
    "describe-schemas": {"destructive": False, "requires_root": False},
    "detect": {"destructive": False, "requires_root": False},
    "health": {"destructive": False, "requires_root": False},
    "history": {"destructive": False, "requires_root": False},
    "status": {"destructive": False, "requires_root": False},
    "cancel": {
        "destructive": False,
        "requires_root": False,
        "destructive_when": (
            "never itself, but stopping a run mid-write leaves the device "
            "partially written"
        ),
    },
    "endurance": {
        "destructive": False,
        "requires_root": False,
        "destructive_when": "never while the engine is unimplemented",
    },
    "quick-test": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": (
            "always: f3probe writes probe patterns across the device, and an "
            "interrupted run may not finish restoring them"
        ),
    },
    "surface-scan": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "--mode destructive; readonly performs no writes",
    },
    "filesystem-check": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "--force, which turns off read-only mode",
    },
    "performance": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "always: the fio jobs write to the device",
    },
    "workload-smallfiles": {
        "destructive": True,
        "requires_root": False,
        "destructive_when": "always, though it writes files through the mounted filesystem",
    },
    "full-capacity-test": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "always: the whole span is overwritten",
    },
    "image-flash": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "always: dd overwrites the device",
    },
    "pipeline": {
        "destructive": True,
        "requires_root": True,
        "destructive_when": "when the negotiated plan contains a writing stage",
    },
}


def _normalize_default(value: Any) -> Any:
    if value is None:
        return None
    return value


def _describe_click_param(param: click.Parameter) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "name": param.name,
        "required": param.required,
        "default": _normalize_default(getattr(param, "default", None)),
        "description": getattr(param, "help", None),
    }
    param_type = getattr(param, "type", None)
    type_name = getattr(param_type, "name", None) if param_type else None
    if isinstance(param, click.Option):
        descriptor["flags"] = list(getattr(param, "opts", []))
        descriptor["type"] = type_name or "string"
        choices = getattr(param_type, "choices", None)
        if choices:
            descriptor["allowed_values"] = list(choices)
    else:
        descriptor["type"] = type_name or "string"
    return descriptor


def _walk_click_command(
    command: click.Command,
    mapping: dict[str, click.Command],
    prefix: Sequence[str] = (),
) -> None:
    segments = list(prefix)
    if command.name and (prefix or command.name != APP_NAME):
        segments.append(command.name)
        mapping[" ".join(segments).strip()] = command
    if hasattr(command, "commands"):
        children = getattr(command, "commands", {}) or {}
        for child_name in sorted(children.keys()):
            child_cmd = children.get(child_name)
            if child_cmd is None:
                continue
            _walk_click_command(child_cmd, mapping, tuple(segments))


def _collect_command_map() -> dict[str, click.Command]:
    mapping: dict[str, click.Command] = {}
    root_command = get_command(app)
    _walk_click_command(root_command, mapping, ())
    return mapping


def _result_schema_name(command_name: str) -> str | None:
    """The schema describing this command's `data` payload, if one ships.

    `cli_response.schema.json` constrains the envelope but leaves `data` open,
    so without this a caller could confirm a response was a CLIResponse but not
    that it was a valid `quick-test` result.
    """

    candidate = f"{command_name.replace(' ', '-')}.result.schema.json"
    if (paths.DEFAULT_SCHEMAS_DIR / candidate).is_file():
        return candidate
    return None


def _describe_click_command(full_name: str, command: click.Command) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": full_name,
        "summary": (command.help or "").strip(),
        "destructive": False,
        "requires_root": False,
        "destructive_when": None,
        "result_schema": None,
        "arguments": [],
        "options": [],
    }
    metadata.update(_DESCRIBE_OVERRIDES.get(full_name, {}))
    metadata["result_schema"] = _result_schema_name(full_name)
    requirements = _TOOL_REQUIREMENTS.get(full_name, {})
    metadata["required_tools"] = list(requirements.get("required_tools", []))
    metadata["optional_tools"] = list(requirements.get("optional_tools", []))
    metadata["degradation"] = requirements.get("degradation")
    for param in command.params:
        descriptor = _describe_click_param(param)
        if isinstance(param, click.Option):
            metadata["options"].append(descriptor)
        else:
            metadata["arguments"].append(descriptor)
    return metadata


def _build_describe_registry() -> dict[str, dict[str, Any]]:
    mapping = _collect_command_map()
    return {
        name: _describe_click_command(name, command)
        for name, command in sorted(mapping.items())
    }


def _get_command_description(name: str) -> dict[str, Any]:
    registry = _build_describe_registry()
    metadata = registry.get(name)
    if metadata is None:
        raise ArgumentError(f"Command not found: {name}")
    return metadata


def _describe_available_commands() -> list[dict[str, Any]]:
    return list(_build_describe_registry().values())


def _parse_conv_flags(value: str | None) -> tuple[str, ...]:
    if not value:
        return ("fsync",)
    flags = tuple(flag.strip() for flag in value.split(",") if flag.strip())
    return flags or ("fsync",)


def _determine_global_options(
    ctx: typer.Context,
    output: str | None,
    non_interactive: bool | None,
    yes: bool | None,
    dry_run: bool | None,
) -> None:
    mode = os.environ.get("TFQA_MODE", "").lower()
    default_output = "json" if mode == "ai" else "human"
    default_non_interactive = mode == "ai"
    if os.environ.get("TFQA_NON_INTERACTIVE") == "1":
        default_non_interactive = True

    ctx.ensure_object(dict)
    ctx.obj["global"] = {
        "output": output or default_output,
        "non_interactive": (
            non_interactive if non_interactive is not None else default_non_interactive
        ),
        "yes": yes if yes is not None else False,
        "dry_run": dry_run if dry_run is not None else False,
    }


def _resolve_output(ctx: typer.Context, explicit: str | None) -> str:
    if explicit:
        return explicit
    return ctx.obj.get("global", {}).get("output", "human")


def _resolve_dry_run(ctx: typer.Context, explicit: bool) -> bool:
    """True when either the global `--dry-run` or the command's own flag is set.

    The global flag used to be parsed and stored but never read, so
    `tfqa --dry-run <destructive command>` executed for real.
    """

    if explicit:
        return True
    return bool(ctx.obj.get("global", {}).get("dry_run", False))


def _plan_safety_preview(
    ctx: typer.Context, device: DeviceInfo, force: bool, confirmed: bool | None = None
) -> dict[str, Any]:
    """Report whether the real run would clear the destructive-operation guard.

    A dry run is the natural place to find out that the device is mounted,
    rather than discovering it only when the write is attempted.
    """

    try:
        _assert_device_safe(ctx, device, force, confirmed=confirmed)
    except TFQAError as exc:
        # `exc.message` is prefixed prose ("Device unsafe for destructive
        # operation: ..."); details carries the bare reason plus the structured
        # context. Keep the keys identical in both branches so automation does
        # not have to handle two shapes.
        details = dict(exc.details)
        reason = details.pop("reason", None) or exc.message
        return {
            "would_run": False,
            "error_code": exc.error_code,
            "reason": reason,
            "details": details,
        }
    return {"would_run": True, "error_code": None, "reason": None, "details": {}}


def _emit_dry_run(
    ctx: typer.Context,
    command_name: str,
    device: DeviceInfo,
    plan: dict[str, Any],
    actual_output: str,
    *,
    force: bool = False,
    confirmed: bool | None = None,
    label: str | None = None,
    check_safety: bool = True,
) -> None:
    """Print the plan a destructive command would execute, and stop.

    `check_safety=False` is for commands that legitimately run on a mounted
    device (workload-smallfiles), where a refusal preview would be misleading.
    """

    # Guarantee the key rather than relying on each command to include it:
    # workload-smallfiles used `device_path` while everything else used
    # `device`, so a caller could not read the target out of a plan uniformly.
    plan = {"device": device.path, **plan}
    if check_safety:
        plan = {
            **plan,
            "safety": _plan_safety_preview(ctx, device, force, confirmed),
        }
    resp = CLIResponse(
        status="ok",
        command=command_name,
        message=f"Dry run: {label or command_name} plan prepared for {device.path}.",
        data={"plan": plan},
    )
    if actual_output == "json":
        print(resp.model_dump_json())
        return
    print(resp.message)
    print(f"Plan: {plan}")


DETACH_HELP = (
    "Start the run in the background and print its run_id immediately. "
    "Follow it with `tfqa status <run_id>`."
)


def _detach(ctx: typer.Context, run_id: str, log_dir: Path | None) -> int:
    """Re-run this invocation in a child process, without --detach.

    Returns the child pid. The parent records the run and exits, so a caller
    driving the CLI is not held open for the hours a full-span write takes.
    """

    argv = [arg for arg in sys.argv[1:] if arg not in ("--detach", "--background")]
    child_environment = {**os.environ, "TFQA_RUN_ID": run_id}
    if log_dir:
        child_environment["TFQA_LOG_DIR"] = str(log_dir)
    process = subprocess.Popen(  # noqa: S603 - re-running ourselves, not user input
        [sys.executable, "-m", "tfqa.cli.main", *argv],
        env=child_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process.pid


def _begin_run(
    command_name: str,
    run_id: str,
    device_path: str | None,
    log_dir: Path | None,
    *,
    pid: int | None = None,
    total_bytes: int = 0,
) -> runstate.RunStatus:
    status = runstate.RunStatus(
        run_id=run_id,
        command=command_name,
        device_path=device_path,
        pid=pid if pid is not None else os.getpid(),
        total_bytes=total_bytes,
    )
    runstate.write(status, log_dir)
    return status


def _finish_run(
    status: runstate.RunStatus,
    log_dir: Path | None,
    *,
    state: runstate.RunState,
    message: str | None = None,
    error_code: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> None:
    status.state = state
    status.finished_at = time.time()
    status.message = message
    status.error_code = error_code
    if metrics:
        status.metrics = {
            k: v for k, v in metrics.items() if isinstance(v, (int, float))
        }
    runstate.write(status, log_dir)


def _progress_recorder(
    status: runstate.RunStatus, log_dir: Path | None, phase: str
) -> Callable[[int, int], None]:
    """Persist progress, throttled so a fast loop does not thrash the disk."""

    last = [0.0]

    def record(done: int, total: int) -> None:
        status.phase = phase
        status.completed_bytes = done
        status.total_bytes = total
        status.wrote_to_device = status.wrote_to_device or done > 0
        now = time.time()
        if now - last[0] >= 1.0 or done >= total:
            last[0] = now
            runstate.write(status, log_dir)

    return record


def _assert_device_safe(
    ctx: typer.Context,
    device: DeviceInfo,
    force: bool,
    *,
    confirmed: bool | None = None,
) -> None:
    """Refuse to write to a mounted device or a system disk.

    Every command that writes raw blocks must call this before touching the
    device. Overriding requires both `--force` and `--yes`, so a stray `--force`
    left in a script cannot on its own arm a destructive run.

    `confirmed` overrides the global `--yes` for commands that also expose their
    own `--yes` option.
    """

    if confirmed is None:
        confirmed = bool(ctx.obj.get("global", {}).get("yes", False))
    safety_mod.assert_safe_for_destructive(device, force=force, yes=confirmed)


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        candidate = int(value)
    except Exception:
        return default
    return candidate if candidate >= 1 else default


def _coerce_non_negative_float(value: Any, default: float) -> float:
    try:
        candidate = float(value)
    except Exception:
        return default
    return candidate if candidate >= 0 else default


def _coerce_retry_factor(value: Any, default: float) -> float:
    try:
        candidate = float(value)
    except Exception:
        return default
    return candidate if candidate >= 1 else default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in ("1", "true", "yes", "on")
    return default


def _human_readable_bytes(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    magnitude = float(value)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if magnitude < 1024 or unit == units[-1]:
            return f"{magnitude:.2f} {unit}"
        magnitude /= 1024
    return f"{magnitude:.2f} PB"


def _human_readable_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "n/a"
    return f"{float(seconds):.2f} s"


def _format_quick_test_metrics(metrics: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    def add_line(key: str, label: str, formatter: Any | None = None) -> None:
        value = metrics.get(key)
        if value is None:
            return
        formatter_func = formatter or (lambda v: str(v))
        lines.append(f"{label}: {formatter_func(value)}")

    add_line("estimated_real_size_bytes", "Estimated real size", _human_readable_bytes)
    add_line("test_size_bytes", "Test size", _human_readable_bytes)
    add_line("coverage_percent", "Coverage", lambda v: f"{float(v):.1f}%")
    add_line("duration_seconds", "Duration", _human_readable_duration)
    add_line("throughput_mbps", "Throughput", lambda v: f"{float(v):.2f} MB/s")

    for key, value in metrics.items():
        if key in {
            "estimated_real_size_bytes",
            "test_size_bytes",
            "coverage_percent",
            "duration_seconds",
            "throughput_mbps",
        }:
            continue
        lines.append(f"{key}: {value}")

    return lines


_CLI_CONSOLE = Console()


def _print_boxed_section(title: str, lines: Sequence[str]) -> None:
    if not lines:
        return
    panel = Panel(
        "\n".join(lines),
        title=title,
        title_align="left",
        expand=False,
        border_style="bright_cyan",
        padding=(0, 1),
    )
    _CLI_CONSOLE.print(panel)


def _build_cli_overrides(
    log_dir: Path | None, profiles_dir: Path | None
) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if log_dir:
        overrides["log_dir"] = str(log_dir)
    if profiles_dir:
        overrides["profiles_dir"] = str(profiles_dir)
    return overrides


def _generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_log_path(log_dir: Path | None, run_id: str | None) -> Path | None:
    if log_dir and run_id:
        return Path(log_dir) / f"run-{run_id}.jsonl"
    return None


def _ensure_config(ctx: typer.Context) -> ConfigModel:
    ctx.ensure_object(dict)
    config = ctx.obj.get("config")
    if not config:
        config = cfg_mod.load_config()
        ctx.obj["config"] = config
    return config


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config_path: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Additional TOML config file to load.",
    ),
    log_dir: Path | None = typer.Option(
        None,
        "--log-dir",
        help="Override the TFQA log directory.",
    ),
    profiles_dir: Path | None = typer.Option(
        None,
        "--profiles-dir",
        help="Override the directory that stores test profiles.",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Preferred output format.",
    ),
    non_interactive: bool | None = typer.Option(
        None,
        "--non-interactive/--interactive",
        help="Disable prompts when set to True.",
    ),
    yes: bool | None = typer.Option(
        None,
        "--yes/--no-yes",
        "-y",
        help="Skip confirmations for destructive operations.",
    ),
    dry_run: bool | None = typer.Option(
        None,
        DRY_RUN_FLAG,
        help="Show plan without executing destructive work.",
    ),
) -> None:
    ctx.ensure_object(dict)
    _determine_global_options(ctx, output, non_interactive, yes, dry_run)
    overrides = _build_cli_overrides(log_dir, profiles_dir)
    if ctx.obj.get("config") is None:
        paths = [config_path] if config_path else None
        ctx.obj["config"] = cfg_mod.load_config(
            config_paths=paths, cli_overrides=overrides
        )


@app.command(name="detect")
def detect(
    ctx: typer.Context,
    output: str | None = typer.Option(None, help=DETECT_OUTPUT_HELP),
) -> None:
    """Detect block devices on the host."""
    command_name = "detect"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        device_list = devices_mod.discover_devices()
        data: dict[str, object] = {"devices": [d.model_dump() for d in device_list]}
        if _config.log_dir:
            data["log_dir"] = str(_config.log_dir)
        if getattr(_config, "profiles_dir", None):
            data["profiles_dir"] = str(_config.profiles_dir)

        if actual_output == "json":
            resp = CLIResponse(
                status="ok",
                command=command_name,
                message=f"{len(device_list)} block devices detected.",
                data=data,
            )
            print(resp.model_dump_json())
            return

        print(f"{len(device_list)} block devices detected:\n")
        for d in device_list:
            print(
                f"- {d.path}  {d.size_bytes} bytes  {d.name}  removable={d.is_removable}"
            )

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="validate-schemas")
def validate_schemas(
    ctx: typer.Context,
    schema: str | None = typer.Option(
        None,
        "--schema",
        "-s",
        help="Schema name (with or without .schema.json) to validate.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Validate each JSON schema under the schema directory."""

    command_name = "validate-schemas"
    try:
        actual_output = _resolve_output(ctx, output)
        config = _ensure_config(ctx)
        entries = _load_schema_metadata(config)
        if schema:
            entries = _filter_schema_entries(entries, schema)
        schema_map: dict[Path, dict[str, Any]] = {
            Path(entry["path"]): entry for entry in entries if entry.get("path")
        }
        if not schema_map:
            schema_dir = _schema_directory_from_config(config)
            raise TFQAError(
                f"No JSON schemas found in {schema_dir}.",
                "INVALID_ARGUMENT",
            )
        paths = sorted(schema_map.keys())

        validation_results: list[dict[str, Any]] = []
        validation_errors: list[dict[str, str]] = []
        for schema_path in paths:
            metadata = schema_map.get(schema_path, {})
            file_result: dict[str, Any] = {
                "name": metadata.get("name") or schema_path.name,
                "path": str(schema_path),
                "title": metadata.get("title"),
                "schema_version": metadata.get("schema_version"),
                "status": "ok",
                "errors": [],
                "hints": [],
                "metadata_issues": [],
            }
            schema_errors: list[str] = []
            schema_hints: list[str] = []
            metadata_issues: list[str] = []

            try:
                raw = cast(
                    dict[str, Any], json.loads(schema_path.read_text(encoding="utf-8"))
                )
            except json.JSONDecodeError as exc:
                message = f"JSON decode error: {exc.msg}"
                schema_errors.append(message)
                schema_hints.append(
                    "Fix the syntax (missing commas, quotes, or braces) so the schema can be parsed."
                )
                raw = None
            except Exception as exc:  # pragma: no cover - defensive
                message = str(exc)
                schema_errors.append(message)
                schema_hints.append(
                    "Ensure the schema file is readable and contains JSON."
                )
                raw = None

            if raw is None:
                if not schema_errors:
                    schema_errors.append("Empty schema file.")
                    schema_hints.append(
                        "Populate the schema with a valid draft-07 document."
                    )
            else:
                try:
                    Draft7Validator.check_schema(raw)
                except SchemaError as exc:
                    schema_errors.append(str(exc))
                    schema_hints.append(
                        "Use draft-07 keywords/definitions and revalidate with jsonschema."
                    )

            if not metadata.get("title"):
                metadata_issues.append("title")
                schema_hints.append(
                    "Add a `title` property so automation understands the schema context."
                )
            if not metadata.get("schema_version"):
                metadata_issues.append("schema_version")
                schema_hints.append(
                    "Add a `schema_version` to capture compatibility for consumers."
                )
            if not metadata.get("title") and not metadata.get("schema_version"):
                schema_hints.append(
                    "Run `tfqa describe-schemas --output json` to double-check metadata."
                )

            if metadata_issues:
                file_result["metadata_issues"] = metadata_issues
            if schema_errors:
                file_result["status"] = "error"
                file_result["errors"] = schema_errors
                file_result["hints"] = schema_hints
                validation_errors.extend(
                    {"schema": str(schema_path), "error": err} for err in schema_errors
                )
            else:
                file_result["hints"] = schema_hints
            validation_results.append(file_result)

        failed_count = sum(
            1 for result in validation_results if result["status"] != "ok"
        )
        status: Literal["ok", "fail"] = "ok" if failed_count == 0 else "fail"
        message = f"Validated {len(validation_results)} schema(s)."
        if status == "fail":
            message = f"{failed_count} schema(s) had errors out of {len(validation_results)} validated."

        resp = CLIResponse(
            status=status,
            command=command_name,
            message=message,
            data={
                "validated": len(validation_results),
                "failed": failed_count,
                "files": validation_results,
                "errors": validation_errors,
            },
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print("Schemas validated:")
        for result in validation_results:
            status_label = result["status"].upper()
            print(f"- {result['path']} [{status_label}]")
            metadata_issues = result.get("metadata_issues") or []
            if metadata_issues:
                issue_list = ", ".join(metadata_issues)
                print(f"    metadata issues: {issue_list}")
            errors = result.get("errors") or []
            hints = result.get("hints") or []
            if errors:
                for error in errors:
                    print(f"    error: {error}")
            elif hints:
                for hint in hints:
                    print(f"    hint: {hint}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="lint-schemas")
def lint_schemas(
    ctx: typer.Context,
    schema: str | None = typer.Option(
        None,
        "--schema",
        "-s",
        help="Schema name (with or without .schema.json) to lint.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Ensure each schema declares the required metadata for automation."""

    command_name = "lint-schemas"
    try:
        actual_output = _resolve_output(ctx, output)
        config = _ensure_config(ctx)
        entries = _load_schema_metadata(config)
        if schema:
            entries = _filter_schema_entries(entries, schema)
        if not entries:
            schema_dir = _schema_directory_from_config(config)
            raise TFQAError(
                f"No JSON schemas found in {schema_dir}.",
                "INVALID_ARGUMENT",
            )

        issues = _schema_metadata_issues(entries)

        status: Literal["ok", "fail"] = "ok" if not issues else "fail"
        message = (
            f"All {len(entries)} schema(s) expose title + schema_version."
            if status == "ok"
            else f"{len(issues)} schema(s) missing required metadata."
        )
        resp = CLIResponse(
            status=status,
            command=command_name,
            message=message,
            data={
                "inspected": len(entries),
                "issues": issues,
            },
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print("Schemas inspected:")
        issue_map = {issue.get("path"): issue for issue in issues}
        for entry in entries:
            path = entry.get("path") or entry.get("name")
            if not path:
                continue
            issue = issue_map.get(path)
            if issue:
                fields = ", ".join(issue["missing_fields"])
                print(f"- {path}: missing {fields}")
            else:
                print(f"- {path}: ok")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="quick-test")
def quick_test(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    free_space_only: bool = typer.Option(
        True,
        "--free-space-only/--no-free-space-only",
        help="Restrict the test to unused regions of the device.",
    ),
    dry_run: bool = typer.Option(
        False,
        DRY_RUN_FLAG,
        help="Show the quick-test plan without executing.",
    ),
    f3_timeout: float = typer.Option(
        120.0,
        "--f3-timeout",
        help="Seconds to wait for `f3probe` to complete (default 120).",
    ),
    force: bool = typer.Option(False, "--force", help=FORCE_HELP),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Run a quick capacity/authenticity check on the provided device."""

    command_name = "quick-test"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "free_space_only": free_space_only,
                    "timeout_seconds": f3_timeout,
                },
                actual_output,
                force=force,
            )
            return

        # f3probe writes probe patterns across the device, so this is a
        # destructive operation even though it restores the blocks afterwards.
        _assert_device_safe(ctx, target_device, force)

        probe_command = quick_capacity.describe_probe_command(target_device)
        progress_event: Event | None = None
        progress_thread: Thread | None = None
        if actual_output != "json":
            print("Starting quick-test; this may take several minutes.")
            quoted_command = " ".join(shlex.quote(part) for part in probe_command)
            print(f"  Running f3probe: {quoted_command}")
            print("  Arguments: device path from --device, timeout from --f3-timeout")
            progress_event = Event()

            def _report_progress() -> None:
                start_time = monotonic()
                while not progress_event.wait(10):
                    elapsed = int(monotonic() - start_time)
                    print(
                        f"[quick-test] f3probe running... {elapsed}s elapsed (timeout {f3_timeout}s)"
                    )

            progress_thread = Thread(target=_report_progress, daemon=True)
            progress_thread.start()

        try:
            payload: dict[str, Any] = quick_capacity.run_quick_capacity(
                target_device,
                free_space_only=free_space_only,
                timeout_seconds=f3_timeout,
            )
        finally:
            if progress_thread and progress_event:
                progress_event.set()
                progress_thread.join()
        fake_detected = bool(payload.get("fake_detected"))
        payload_status = payload.get("status")
        cli_status: Literal["ok", "fail", "error"]
        if payload_status == "error":
            cli_status = "error"
        elif payload_status == "fail" or fake_detected:
            cli_status = "fail"
        else:
            cli_status = "ok"

        message_raw = payload.get("message")
        message: str | None = message_raw if isinstance(message_raw, str) else None
        if not message:
            if fake_detected:
                message = f"Fake capacity detection: potential counterfeit on {target_device.path}."
            else:
                message = f"Quick test completed for {target_device.path}."

        metrics: dict[str, float] = {}
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[key] = float(value)
        details: dict[str, Any] = cast(dict[str, Any], payload.get("details", {}))

        run_id = _generate_run_id()
        event: dict[str, Any] = {
            "phase": "quick-test",
            "status": cli_status,
            "device": target_device.model_dump(),
            "metrics": metrics,
            "details": details,
        }
        log_path = logging_mod.emit_event(
            run_id,
            event,
            log_dir=_config.log_dir,
        )

        resp = CLIResponse(
            status=cli_status,
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=message,
            data=dict(payload),
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        if metrics:
            _print_boxed_section(
                "Quick test metrics", _format_quick_test_metrics(metrics)
            )

        probe_cmd = details.get("probe_command") or probe_command
        if probe_cmd:
            quoted_probe = " ".join(shlex.quote(part) for part in probe_cmd)
            timeout_value = details.get("timeout_seconds", f3_timeout)
            invocation_lines = [
                f"Command: {quoted_probe}",
                f"Timeout: {timeout_value}s (from --f3-timeout)",
                f"Device: {target_device.path} (via --device)",
            ]
            _print_boxed_section("f3probe invocation", invocation_lines)

        stdout = details.get("stdout")
        if stdout:
            stdout_lines = [line for line in stdout.strip().splitlines() if line]
            if stdout_lines:
                _print_boxed_section("f3probe stdout", stdout_lines)
        stderr = details.get("stderr")
        if stderr:
            stderr_lines = [line for line in stderr.strip().splitlines() if line]
            if stderr_lines:
                _print_boxed_section("f3probe stderr", stderr_lines)

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="performance")
def performance(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    duration: float = typer.Option(30.0, "--duration", help="Duration seconds."),
    mode: str = typer.Option(
        "sequential",
        "--mode",
        help="Benchmark mode: sequential or random.",
    ),
    random_bs: str = typer.Option(
        "4k",
        "--random-bs",
        help="Block size to use for random I/O mode.",
    ),
    random_iodepth: int = typer.Option(
        32,
        "--random-iodepth",
        help="I/O depth to use for random mode.",
    ),
    random_rw: str = typer.Option(
        "randrw",
        "--random-rw",
        help="Read/write mix for random mode (e.g., randrw, randread).",
    ),
    random_read_percentage: int = typer.Option(
        50,
        "--random-read-percentage",
        help="Read percentage for random rw mix (0-100).",
    ),
    force: bool = typer.Option(False, "--force", help=FORCE_HELP),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the benchmark plan without running it."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Run a synthetic sequential performance benchmark."""

    command_name = "performance"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        normalized_mode = mode.lower()
        # Validate before the dry-run return so a plan is never advertised for
        # an invocation the real run would reject.
        if normalized_mode not in ("sequential", "random"):
            raise ArgumentError(
                message="Unknown performance mode; choose sequential or random."
            )

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "mode": normalized_mode,
                    "duration_seconds": duration,
                    "random_block_size": random_bs,
                    "random_iodepth": random_iodepth,
                    "random_rw": random_rw,
                    "random_read_percentage": random_read_percentage,
                },
                actual_output,
                force=force,
            )
            return

        _assert_device_safe(ctx, target_device, force)

        if normalized_mode == "random":
            payload = perf_random.run_random_performance(
                target_device,
                duration_seconds=duration,
                block_size=random_bs,
                io_depth=random_iodepth,
                rw_mix=random_rw,
                random_read_percentage=random_read_percentage,
            )
            message = f"Random performance test completed for {target_device.path}"
        else:  # sequential; the mode was validated above
            payload = perf_basic.run_seq_performance(
                target_device, duration_seconds=duration
            )
            message = f"Sequential performance test completed for {target_device.path}"

        # Reflect what the engine reported. This was hardcoded to "ok", so a
        # failing benchmark would still have been announced as a success.
        # Distinguish a benchmark that failed from one that errored, so a
        # caller can tell "the card is slow" from "the run broke".
        payload_status = payload.get("status")
        perf_status: Literal["ok", "fail", "error"] = (
            "ok"
            if payload_status in (None, "ok")
            else "fail"
            if payload_status == "fail"
            else "error"
        )
        resp = CLIResponse(
            status=perf_status,
            command=command_name,
            message=message,
            device={"path": target_device.path},
            data=dict(payload),
            log_path=_build_log_path(_config.log_dir, _generate_run_id()),
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        metrics = payload.get("metrics", {})
        print(resp.message)
        if normalized_mode == "random":
            print(f"Random read throughput: {metrics.get('random_read_mbps')} MB/s")
            print(f"Random write throughput: {metrics.get('random_write_mbps')} MB/s")
            print(
                f"Block size: {metrics.get('block_size')} rw_mix: {metrics.get('rw_mix')}"
            )
        else:
            print(f"Read throughput: {metrics.get('sequential_read_mbps')} MB/s")
            print(f"Write throughput: {metrics.get('sequential_write_mbps')} MB/s")
        print(f"Duration: {duration}s")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="surface-scan")
def surface_scan(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    mode: str = typer.Option(
        "readonly",
        "--mode",
        "-m",
        help="Scan mode: readonly or destructive.",
    ),
    passes: int = typer.Option(
        1,
        "--passes",
        "-p",
        help="Number of passes to attempt during the scan.",
    ),
    duration: float = typer.Option(
        60.0,
        "--duration",
        help="Duration to attribute to the scan for reporting.",
    ),
    block_size: int = typer.Option(
        4096,
        "--block-size",
        "-b",
        help="Block size in bytes used by the scan.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Allow destructive scans (required for mode=destructive).",
    ),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the scan plan without running it."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Inspect a device surface via a badblocks-powered scan."""

    command_name = "surface-scan"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        normalized_mode = mode.lower()
        if normalized_mode not in ("readonly", "destructive"):
            raise ArgumentError(
                "Invalid surface scan mode; choose readonly or destructive."
            )
        if normalized_mode == "destructive" and not force:
            raise ArgumentError("Destructive scans require --force to opt in.")

        # After argument validation so a dry run still reports bad arguments,
        # but before the safety guard so it can preview a refusal.
        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "mode": normalized_mode,
                    "passes": passes,
                    "duration_seconds": duration,
                    "block_size": block_size,
                    "force": force,
                },
                actual_output,
                force=force,
                # A read-only sweep never writes, so it has nothing to clear.
                check_safety=normalized_mode == "destructive",
            )
            return

        if normalized_mode == "destructive":
            # A read-only sweep never writes, so only the destructive mode has
            # to clear the mounted/system-disk checks.
            _assert_device_safe(ctx, target_device, force)

        run_id = _generate_run_id()
        scan_result = surface_scan_mod.run_surface_scan(
            target_device,
            pass_count=passes,
            duration_seconds=duration,
            mode=normalized_mode,  # type: ignore[arg-type]
            block_size=block_size,
        )
        log_path = logging_mod.emit_event(
            run_id,
            {
                "phase": "surface",
                "device_path": target_device.path,
                "mode": normalized_mode,
                "metrics": scan_result.get("metrics", {}),
                "details": scan_result.get("details", {}),
            },
            log_dir=_config.log_dir,
        )

        resp = CLIResponse(
            status="ok",
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=f"Surface scan ({normalized_mode}) completed for {target_device.path}",
            data=dict(scan_result),
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        metrics = scan_result.get("metrics", {})
        print(resp.message)
        print(f"Coverage: {metrics.get('coverage_percent')}%")
        print(f"Passes: {metrics.get('pass_count')} duration: {duration}s")
        tool = scan_result.get("details", {}).get("tool")
        if tool:
            print(f"Tool: {tool}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="filesystem-check")
def filesystem_check(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    read_only: bool = typer.Option(
        True, "--read-only/--no-read-only", help="Run fsck in read-only mode."
    ),
    force: bool = typer.Option(
        False, "--force", help="Allow fsck to make repairs (requires safety overrides)."
    ),
    timeout: float = typer.Option(
        120.0, "--timeout", help="Timeout for fsck in seconds."
    ),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the fsck plan without running it."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Run fsck to validate the filesystem metadata for the target device."""

    command_name = "filesystem-check"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        run_id = _generate_run_id()
        # `--force` turns read-only mode off regardless of --read-only, so the
        # guard has to key off the effective value. Keying it off the raw flag
        # let `--force` alone (with the default --read-only) run a repair-capable
        # fsck on a mounted device without any safety check.
        effective_read_only = read_only and not force

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "read_only": effective_read_only,
                    "force": force,
                    "timeout_seconds": timeout,
                },
                actual_output,
                force=force,
                check_safety=not effective_read_only,
            )
            return

        if not effective_read_only:
            _assert_device_safe(ctx, target_device, force)

        fsck_result = run_fsck(
            target_device.path,
            read_only=effective_read_only,
            force=force,
            timeout_seconds=timeout,
        )

        metrics = {
            "fsck_returncode": fsck_result.returncode,
            "fsck_clean": int(fsck_result.clean),
            "fsck_errors_fixed": int(fsck_result.errors_fixed),
            "fsck_duration_seconds": fsck_result.duration_seconds,
        }
        event = {
            "phase": "filesystem",
            "device_path": target_device.path,
            "metrics": metrics,
            "details": fsck_result.model_dump(),
        }
        log_path = logging_mod.emit_event(run_id, event, log_dir=_config.log_dir)

        history_mod.record_run(
            command=command_name,
            run_id=run_id,
            device_path=target_device.path,
            status=fsck_result.status,
            message="Filesystem check completed",
            stage_count=1,
            metadata={
                "read_only": read_only,
                "force": force,
                "timeout_seconds": timeout,
            },
            log_path=log_path,
        )

        cli_status: Literal["ok", "fail", "error"]
        if fsck_result.status == "error":
            cli_status = "error"
        elif fsck_result.status == "warning":
            cli_status = "fail"
        else:
            cli_status = "ok"

        message = (
            "Filesystem check succeeded without issues."
            if fsck_result.clean
            else "Filesystem check reported potential problems."
        )

        resp = CLIResponse(
            status=cli_status,
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=message,
            data={"result": fsck_result.model_dump()},
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(f"Return code: {fsck_result.returncode}")
        print(f"Clean: {fsck_result.clean}")
        if fsck_result.errors_fixed:
            print("Errors were fixed during fsck.")
        if fsck_result.operational_error:
            print("Operational error detected; see logs for details.")
        if fsck_result.needs_reboot:
            print("fsck indicated the system should reboot.")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="image-flash")
def image_flash(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    image_path: Path = typer.Option(
        ..., "--image-path", "-i", exists=True, file_okay=True, dir_okay=False
    ),
    block_size: str = typer.Option(
        "4M",
        "--block-size",
        help="Block size to use when flashing the image.",
    ),
    conv_flags: str | None = typer.Option(
        None,
        "--conv-flags",
        help="Comma-separated conv flags for dd (e.g., fsync,noerror).",
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Verify the image after flashing.",
    ),
    write_timeout: float = typer.Option(
        600.0,
        "--write-timeout",
        help="Timeout in seconds for the write phase.",
    ),
    verify_timeout: float = typer.Option(
        300.0,
        "--verify-timeout",
        help="Timeout in seconds for the verify phase.",
    ),
    force: bool = typer.Option(False, "--force", help=FORCE_HELP),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the flash plan without writing the image."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Flash an image onto the selected device and optionally verify it."""

    command_name = "image-flash"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        conv_opts = _parse_conv_flags(conv_flags)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "image_path": str(image_path),
                    "block_size": block_size,
                    "conv_flags": list(conv_opts),
                    "verify": verify,
                    "write_timeout": write_timeout,
                    "verify_timeout": verify_timeout,
                },
                actual_output,
                force=force,
            )
            return

        # dd overwrites the whole device; never let this reach a mounted
        # filesystem or the system disk without an explicit override.
        _assert_device_safe(ctx, target_device, force)
        run_id = _generate_run_id()

        result = run_image_flash(
            str(image_path),
            target_device.path,
            block_size=block_size,
            conv_flags=conv_opts,
            write_timeout=write_timeout,
            verify_timeout=verify_timeout,
            verify=verify,
        )

        status_raw = result.get("status", "ok")
        status = str(status_raw) if isinstance(status_raw, str) else "ok"
        cli_status: Literal["ok", "fail", "error"] = "ok" if status == "ok" else "fail"
        message = (
            f"Image flash completed for {target_device.path}."
            if cli_status == "ok"
            else f"Image flash reported issues for {target_device.path}."
        )

        metrics = cast(dict[str, Any], result.get("metrics", {}))
        details = cast(dict[str, Any], result.get("details", {}))
        event: dict[str, Any] = {
            "phase": "image-flash",
            "device_path": target_device.path,
            "status": status,
            "metrics": metrics,
            "details": details,
        }
        log_path = logging_mod.emit_event(run_id, event, log_dir=_config.log_dir)

        history_mod.record_run(
            command=command_name,
            run_id=run_id,
            device_path=target_device.path,
            status=status,
            message=message,
            stage_count=1,
            metadata={
                "image_path": str(image_path),
                "block_size": block_size,
                "conv_flags": list(conv_opts),
                "verify": verify,
                "write_timeout": write_timeout,
                "verify_timeout": verify_timeout,
            },
            log_path=log_path,
        )

        resp = CLIResponse(
            status=cli_status,
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=message,
            data={
                "result": result,
                "image_path": str(image_path),
                "verify": verify,
            },
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        flashed_metrics: dict[str, object] = cast(
            dict[str, object], result.get("metrics", {})
        )
        if flashed_metrics:
            print(METRICS_LABEL)
            for key, value in flashed_metrics.items():
                print(f"  {key}: {value}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="full-capacity-test")
def full_capacity_test(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
    force: bool = typer.Option(False, "--force", help="Override safety checks."),
    yes: bool | None = typer.Option(
        None,
        "--yes/--no-yes",
        "-y",
        help="Confirm destructive operation (required when using --force).",
    ),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the test plan without writing to the device."
    ),
    block_size: int = typer.Option(
        full_capacity.DEFAULT_BLOCK_SIZE,
        "--block-size",
        help="Bytes written per I/O chunk.",
    ),
    limit_bytes: int | None = typer.Option(
        None,
        "--limit-bytes",
        help="Test only the first N bytes instead of the whole device.",
    ),
    seed: int = typer.Option(
        0, "--seed", help="Pattern seed; change it to re-test with fresh data."
    ),
    detach: bool = typer.Option(False, "--detach", help=DETACH_HELP),
) -> None:
    """Run a destructive full-span write+verify test."""

    command_name = "full-capacity-test"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        # Fall back to the global --yes so `tfqa --yes full-capacity-test
        # --force` behaves like every other command. The local option is
        # tri-state: an explicit --no-yes must revoke the global confirmation
        # rather than be overridden by it.
        actual_yes = (
            bool(yes)
            if yes is not None
            else bool(ctx.obj.get("global", {}).get("yes", False))
        )
        # Same rules the engine applies, before the dry-run branch, so a plan is
        # never advertised for an invocation the real run would reject.
        full_capacity.validate_options(block_size, limit_bytes)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "force": force,
                    "confirmed": actual_yes,
                    "block_size": block_size,
                    "span_bytes": (
                        min(target_device.size_bytes, limit_bytes)
                        if limit_bytes is not None
                        else target_device.size_bytes
                    ),
                    "seed": seed,
                },
                actual_output,
                force=force,
                confirmed=actual_yes,
            )
            return

        _assert_device_safe(ctx, target_device, force, confirmed=actual_yes)

        # A full span on a large card is hours of I/O. Detaching lets a caller
        # start it and poll `tfqa status` instead of holding a connection open
        # past every sane timeout.
        run_id = os.environ.get("TFQA_RUN_ID") or _generate_run_id()
        span = (
            min(target_device.size_bytes, limit_bytes)
            if limit_bytes is not None
            else target_device.size_bytes
        )
        if detach:
            child = _detach(ctx, run_id, _config.log_dir)
            _begin_run(
                command_name,
                run_id,
                target_device.path,
                _config.log_dir,
                pid=child,
                total_bytes=span,
            )
            started = CLIResponse(
                status="ok",
                command=command_name,
                run_id=run_id,
                device={"path": target_device.path},
                message=f"Started in the background as {run_id}.",
                data={"run_id": run_id, "pid": child, "detached": True},
            )
            if actual_output == "json":
                print(started.model_dump_json())
            else:
                print(started.message)
                print(f"Follow it with: tfqa status {run_id}")
            return

        tracked = _begin_run(
            command_name,
            run_id,
            target_device.path,
            _config.log_dir,
            total_bytes=span,
        )
        try:
            payload = full_capacity.run_full_capacity(
                target_device,
                force=force,
                yes=actual_yes,
                block_size=block_size,
                limit_bytes=limit_bytes,
                seed=seed,
                progress=_progress_recorder(tracked, _config.log_dir, "write-verify"),
            )
        except BaseException as exc:
            # Recorded and re-raised, never swallowed. Catching only TFQAError
            # left a run that died on an OSError marked "running" forever,
            # since nothing else would ever update its state.
            _finish_run(
                tracked,
                _config.log_dir,
                state="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                message=getattr(exc, "message", None) or str(exc),
                error_code=getattr(exc, "error_code", None),
            )
            raise
        _finish_run(
            tracked,
            _config.log_dir,
            state="completed" if payload.get("status") == "ok" else "failed",
            message=str(payload.get("message") or ""),
            metrics={k: v for k, v in payload.items() if isinstance(v, (int, float))},
        )
        status: Literal["ok", "fail"] = payload.get("status", "ok")
        default_message = (
            "Full capacity test completed successfully."
            if status == "ok"
            else "Full capacity test reported issues."
        )
        message = payload.get("message") or default_message
        run_id = _generate_run_id()
        data: dict[str, object] = dict(payload)
        data["device"] = {"path": target_device.path}
        log_path = _build_log_path(_config.log_dir, run_id)
        resp = CLIResponse(
            status=status,
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=message,
            data=data,
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(f"Full capacity test {status.upper()} for {target_device.path}")
        print(message)
        coverage = payload["coverage_percent"]
        print(f"Coverage: {coverage}%")
        duration = payload["duration_seconds"]
        print(f"Duration: {duration}s")
        issues = payload["issues"]
        if issues:
            print("Issues:")
            for issue in issues:
                print(f" - {issue}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="combos")
def combos(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None, "--name", "-n", help="Filter by combo name (case-insensitive)."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """List structured workflow combos and metadata for automation clients."""

    command_name = "combos"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        combos_list = workflows_mod.list_combos(_config)
        if name:
            filtered = [
                combo for combo in combos_list if combo.name.lower() == name.lower()
            ]
            if not filtered:
                raise ArgumentError(
                    message=f"Structured workflow combo not found: {name}",
                    details={"combo": name},
                )
            combos_list = filtered

        payload = [
            {
                "name": combo.name,
                "description": combo.description,
                "profile": combo.profile,
                "stages": combo.stages,
                "image_options": combo.image_options,
            }
            for combo in combos_list
        ]

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Discovered {len(payload)} structured workflow combo(s).",
            data={"combos": payload},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        for combo in payload:
            description = combo.get("description") or NO_DESCRIPTION
            print(f"- {combo['name']}: {description}")
            if combo.get("profile"):
                print(f"    profile: {combo['profile']}")
            if combo.get("stages"):
                stages = combo["stages"]
                if stages:
                    print(f"    stages: {', '.join(stages)}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="profiles")
def profiles(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Filter endurance profiles by name (case-insensitive).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """List available endurance profiles for automation clients."""

    command_name = "profiles"
    try:
        actual_output = _resolve_output(ctx, output)
        config = _ensure_config(ctx)
        profiles_list = profile_mod.list_profiles(config)
        if name:
            normalized = name.strip().lower()
            filtered = [
                profile
                for profile in profiles_list
                if profile.get("name", "").lower() == normalized
            ]
            if not filtered:
                raise ArgumentError(
                    message=f"Endurance profile not found: {name}",
                    details={"profile": name},
                )
            profiles_list = filtered

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Discovered {len(profiles_list)} endurance profile(s).",
            data={"profiles": profiles_list},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        _print_profile_list(resp.message, profiles_list)

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="endurance")
def endurance(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    duration: float | None = typer.Option(
        None,
        "--duration",
        help="Duration in seconds for each pass (profiles can override).",
    ),
    passes: int | None = typer.Option(
        None,
        "--passes",
        help="Number of passes to simulate (profiles can override).",
    ),
    force: bool | None = typer.Option(
        None,
        "--force/--no-force",
        help="Override safety restrictions if needed (profiles can set default).",
    ),
    profile: str = typer.Option(
        "default",
        "--profile",
        "-p",
        help="Named endurance profile to load from data/profiles.",
    ),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the endurance plan without running it."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Run a simple endurance/burn-in loop on the provided device."""

    command_name = "endurance"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        run_id = _generate_run_id()
        profile_settings = profile_mod.load_profile(profile, _config)

        base_config = EnduranceConfig(
            duration_seconds=profile_settings.duration_seconds,
            pass_count=profile_settings.pass_count,
            force=profile_settings.force,
            write_pattern=profile_settings.write_pattern,
        )
        overrides: dict[str, Any] = {}
        if duration is not None:
            overrides["duration_seconds"] = duration
        if passes is not None:
            overrides["pass_count"] = passes
        if force is not None:
            overrides["force"] = force
        engine_config = base_config.with_overrides(**overrides)
        # The effective force flag can come from the profile, so evaluate
        # safety against the merged config rather than the raw CLI option.
        effective_force = bool(engine_config.force)
        # Same rules the engine applies, so a dry run never advertises a plan
        # the real invocation would refuse.
        endurance_simple.validate_config(engine_config)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "profile": profile,
                    "duration_seconds": engine_config.duration_seconds,
                    "pass_count": engine_config.pass_count,
                    "write_pattern": engine_config.write_pattern,
                    "force": effective_force,
                },
                actual_output,
                force=effective_force,
                # The engine writes nothing today, so a refusal preview would
                # describe a guard that no longer applies.
                check_safety=False,
            )
            return

        # No _assert_device_safe here on purpose: the engine performs no device
        # I/O, so guarding it only made a mounted card answer DEVICE_UNSAFE
        # before the caller could learn the engine is not implemented. Restore
        # the guard together with the writes.

        run_ctx = RunContext(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
            device=target_device,
            config_profile=profile,
            destructive=False,
            mode="ai" if actual_output == "json" else "human",
            log_dir=_config.log_dir,
        )

        result = endurance_simple.run_simple_endurance(run_ctx, engine_config)
        data = result.model_dump()
        data["profile"] = profile

        cli_status: Literal["ok", "fail", "error"]
        if result.status == "error":
            cli_status = "error"
        else:
            cli_status = "ok" if result.status == "ok" else "fail"
        message = (
            "Endurance simulation completed."
            if result.status == "ok"
            else "Endurance simulation finished with warnings."
        )

        resp = CLIResponse(
            status=cli_status,
            command=command_name,
            message=message,
            device={"path": target_device.path},
            run_id=run_id,
            log_path=result.logs_path,
            data=data,
            error_code=result.error_code,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        metrics: dict[str, object] = cast(dict[str, object], result.metrics)
        print(resp.message)
        print(f"Profile: {profile}")
        print(f"Duration per pass: {result.details.get('duration_seconds')}s")
        print(f"Pass count: {result.details.get('pass_count')}")
        if metrics:
            print(METRICS_LABEL)
            for key, value in metrics.items():
                print(f"  {key}: {value}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


# Ordered worst-last. "skipped" ranks above "ok" so a run where a stage could
# not execute does not report as a clean pass, but below "warning" because
# nothing actually went wrong. Omitting it raised KeyError and took the whole
# default pipeline down with INTERNAL_ERROR.
_STATUS_PRIORITY: dict[TestStatus, int] = {
    "ok": 0,
    "skipped": 1,
    "warning": 2,
    "failed": 3,
    "error": 4,
}


def _aggregate_pipeline_status(results: list[TestResult]) -> TestStatus:
    highest: TestStatus = "ok"
    for result in results:
        # An unknown status must not silently rank as "ok"; treat it as the
        # worst so it cannot be mistaken for a pass.
        rank = _STATUS_PRIORITY.get(result.status, _STATUS_PRIORITY["error"])
        if rank > _STATUS_PRIORITY[highest]:
            highest = result.status
    return highest


def _cli_status_from_pipeline_status(
    status: TestStatus,
) -> Literal["ok", "fail", "error"]:
    if status == "error":
        return "error"
    if status == "failed":
        return "fail"
    # "skipped" and "warning" both exit 0: a stage that could not run is not a
    # failure of the card. The per-stage statuses in the payload say what ran.
    return "ok"


def _pipeline_message(status: TestStatus) -> str:
    if status == "error":
        return "Pipeline aborted due to an error."
    if status == "failed":
        return "Pipeline completed with failures."
    if status == "warning":
        return "Pipeline completed with warnings."
    return "Pipeline completed successfully."


def _build_stage_details(results: list[TestResult]) -> dict[str, dict[str, object]]:
    return {
        result.name: {
            "status": result.status,
            "duration_seconds": result.duration_seconds,
            "metrics": result.metrics,
        }
        for result in results
    }


def _combo_payload(combo: workflows_mod.WorkloadCombo) -> dict[str, object | None]:
    payload: dict[str, object | None] = {
        "name": combo.name,
        "description": combo.description,
        "profile": combo.profile,
        "stages": list(combo.stages),
    }
    if combo.image_options:
        payload["image_options"] = combo.image_options
    return payload


def _normalize_combo_conv_flags(
    combo: workflows_mod.WorkloadCombo | None, cli_value: str | None
) -> tuple[str, ...]:
    if cli_value:
        return _parse_conv_flags(cli_value)
    if combo and combo.image_options:
        raw_flags = combo.image_options.get("conv_flags")
        if isinstance(raw_flags, str):
            return _parse_conv_flags(raw_flags)
        if isinstance(raw_flags, Sequence):
            normalized = tuple(
                str(flag).strip() for flag in raw_flags if flag and str(flag).strip()
            )
            if normalized:
                return normalized
    return DEFAULT_IMAGE_CONV_FLAGS


def _build_image_flash_config(
    *,
    image_path: Path,
    cli_block_size: str,
    cli_conv_flags: str | None,
    cli_verify: bool,
    cli_write_timeout: float,
    cli_verify_timeout: float,
    combo: workflows_mod.WorkloadCombo | None,
) -> tuple[pipeline_mod.ImageFlashConfig, dict[str, object]]:
    combo_options = combo.image_options if combo else None

    block_size = cli_block_size
    if (
        combo_options
        and combo_options.get("block_size")
        and block_size == DEFAULT_IMAGE_BLOCK_SIZE
    ):
        block_size = str(combo_options["block_size"])

    conv_flags = _normalize_combo_conv_flags(combo, cli_conv_flags)

    verify = cli_verify
    if (
        combo_options
        and combo_options.get("verify") is not None
        and verify == DEFAULT_IMAGE_VERIFY
    ):
        verify = bool(combo_options["verify"])

    write_timeout = cli_write_timeout
    if (
        combo_options
        and combo_options.get("write_timeout") is not None
        and write_timeout == DEFAULT_IMAGE_WRITE_TIMEOUT
    ):
        write_timeout = float(combo_options["write_timeout"])

    verify_timeout = cli_verify_timeout
    if (
        combo_options
        and combo_options.get("verify_timeout") is not None
        and verify_timeout == DEFAULT_IMAGE_VERIFY_TIMEOUT
    ):
        verify_timeout = float(combo_options["verify_timeout"])

    image_config = pipeline_mod.ImageFlashConfig(
        image_path=str(image_path),
        block_size=block_size,
        conv_flags=conv_flags,
        verify=verify,
        write_timeout=write_timeout,
        verify_timeout=verify_timeout,
    )

    image_options = {
        "image_path": image_config.image_path,
        "block_size": image_config.block_size,
        "conv_flags": list(image_config.conv_flags),
        "verify": image_config.verify,
        "write_timeout": image_config.write_timeout,
        "verify_timeout": image_config.verify_timeout,
    }
    return image_config, image_options


def _build_pipeline_metadata(
    results: list[TestResult],
    negotiated_stage_plan: list[str],
    requested_stages: list[str] | None,
    stage_details: dict[str, dict[str, object]],
    image_options: dict[str, object] | None,
    combo: workflows_mod.WorkloadCombo | None,
) -> dict[str, object | None]:
    metadata: dict[str, object | None] = {
        "stage_names": [result.name for result in results],
        "stage_statuses": [result.status for result in results],
        "stage_plan": negotiated_stage_plan,
        "metrics": {
            result.name: result.metrics for result in results if result.metrics
        },
        "stage_details": stage_details,
        "requested_stages": requested_stages,
    }
    if image_options:
        metadata["image_options"] = image_options
    if combo:
        metadata["combo"] = _combo_payload(combo)
    return metadata


def _select_report_entry(
    entries: list[dict[str, Any]], run_id: str | None
) -> dict[str, Any]:
    if run_id:
        for entry in entries:
            if entry.get("run_id") == run_id:
                return entry
        raise ArgumentError(f"Run ID not found: {run_id}")
    if not entries:
        raise TFQAError("No history entries recorded yet.", "INVALID_ARGUMENT")
    return entries[0]


def _summarize_history_entry(
    entry: dict[str, Any], config: ConfigModel
) -> tuple[str, dict[str, Any]]:
    run_id = entry.get("run_id")
    if not run_id:
        raise TFQAError("History entry missing run identifier.", "INVALID_ARGUMENT")
    log_path_value = entry.get("log_path")
    actual_log_path = Path(log_path_value) if log_path_value else None
    summary = summary_mod.summarize_run(
        run_id,
        log_dir=config.log_dir if actual_log_path is None else None,
        log_path=actual_log_path,
    )
    return run_id, summary


def _stage_metric_lines(stage_metrics: dict[str, dict[str, Any]]) -> list[str]:
    ordered = sorted(stage_metrics.items())
    lines: list[str] = []
    for stage_name, stage_data in ordered:
        occurrences = stage_data.get("occurrences")
        count = stage_data.get("count")
        occurrence_segment = f", runs={occurrences}" if occurrences is not None else ""
        lines.append(f"- {stage_name} (count={count}{occurrence_segment})")
        status_counts = cast(dict[str, int], stage_data.get("status_counts") or {})
        if status_counts:
            counts_segment = ", ".join(
                f"{key}={value}" for key, value in sorted(status_counts.items())
            )
            lines.append(f"    statuses: {counts_segment}")
        duration_info = cast(dict[str, float | None], stage_data.get("duration") or {})
        duration_count_value = duration_info.get("count")
        duration_count = (
            int(duration_count_value)
            if isinstance(duration_count_value, (int, float))
            else 0
        )
        duration_avg = duration_info.get("average")
        duration_last = duration_info.get("last")
        if duration_count and duration_avg is not None and duration_last is not None:
            lines.append(
                f"    duration avg={duration_avg:.2f}s last={duration_last:.2f}s"
            )
        averages = cast(dict[str, float], stage_data.get("averages") or {})
        for key, value in sorted(averages.items()):
            lines.append(f"    avg {key}: {value:.2f}")
    return lines


def _load_automation_endpoints(config: ConfigModel) -> list[dict[str, Any]]:
    raw = getattr(config, "automation_report", None)
    if not isinstance(raw, dict):
        return []
    endpoints = raw.get("endpoints")
    if not isinstance(endpoints, list):
        return []
    parsed: list[dict[str, Any]] = []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        endpoint_dict = cast(dict[str, Any], endpoint)
        url = endpoint_dict.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        parsed.append(
            {
                "name": str(endpoint_dict.get("name") or url),
                "url": url.strip(),
                "method": str(endpoint_dict.get("method") or "POST").upper(),
                "headers": endpoint_dict.get("headers")
                if isinstance(endpoint_dict.get("headers"), dict)
                else {},
                "timeout": _coerce_non_negative_float(
                    endpoint_dict.get("timeout"), 15.0
                ),
                "max_attempts": _coerce_positive_int(
                    endpoint_dict.get("max_attempts"), 3
                ),
                "backoff_seconds": _coerce_non_negative_float(
                    endpoint_dict.get("backoff_seconds"), 1.0
                ),
                "backoff_factor": _coerce_retry_factor(
                    endpoint_dict.get("backoff_factor"), 2.0
                ),
                "fail_on_error": _coerce_bool(
                    endpoint_dict.get("fail_on_error"), False
                ),
            }
        )
    return parsed


def _push_automation_report(
    payload: dict[str, Any], endpoints: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def _is_retryable_status(status: int | None) -> bool:
        if status is None:
            return True
        return status == 429 or status >= 500

    encoded_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    responses: list[dict[str, Any]] = []
    for endpoint in endpoints:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        for key, value in endpoint.get("headers", {}).items():
            headers[str(key)] = str(value)
        max_attempts = int(endpoint.get("max_attempts", 3))
        max_attempts = max(1, max_attempts)
        timeout_seconds = _coerce_non_negative_float(endpoint.get("timeout"), 15.0)
        current_backoff = _coerce_non_negative_float(
            endpoint.get("backoff_seconds"), 1.0
        )
        backoff_factor = _coerce_retry_factor(endpoint.get("backoff_factor"), 2.0)
        fail_on_error = bool(endpoint.get("fail_on_error", False))
        result: dict[str, Any] = {
            "endpoint": endpoint.get("name", endpoint.get("url")),
            "fail_on_error": fail_on_error,
            "attempts": 0,
            "success": False,
        }
        last_status: int | None = None
        last_error: str | None = None
        for attempt in range(1, max_attempts + 1):
            result["attempts"] = attempt
            req = Request(
                endpoint["url"],
                data=encoded_payload,
                headers=headers,
                method=endpoint.get("method", "POST"),
            )
            try:
                with urlopen(req, timeout=timeout_seconds) as response:
                    body = response.read().decode("utf-8", errors="ignore")
                    status_code = response.getcode()
                    result["status"] = status_code
                    result["body"] = body
                    result["success"] = True
                    last_status = status_code
                    last_error = None
                    break
            except HTTPError as exc:
                retryable = _is_retryable_status(exc.code)
                last_status = exc.code
                last_error = str(exc)
                result["error"] = last_error
                result["status"] = exc.code
                if not retryable:
                    break
            except URLError as exc:
                last_status = None
                last_error = str(exc.reason)
                result["error"] = last_error
            except Exception as exc:  # pragma: no cover - defensive
                last_status = None
                last_error = str(exc)
                result["error"] = last_error
            if attempt < max_attempts and _is_retryable_status(last_status):
                sleep(current_backoff)
                current_backoff *= backoff_factor
        if not result.get("success") and last_error:
            result["last_error"] = last_error
        responses.append(result)
    return responses


def _schema_default_directory() -> Path:
    return paths.DEFAULT_SCHEMAS_DIR


def _schema_directory_from_config(config: ConfigModel) -> Path:
    return paths.schemas_dir(config)


def _load_schema_metadata(config: ConfigModel) -> list[dict[str, Any]]:
    base_dir = _schema_directory_from_config(config)
    if not base_dir.exists() or not base_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for schema_path in sorted(base_dir.glob("*.json")):
        if not schema_path.is_file():
            continue
        try:
            raw = json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        stat = schema_path.stat()
        entry: dict[str, Any] = {
            "name": schema_path.name,
            "path": str(schema_path),
            "title": raw.get("title"),
            "description": raw.get("description"),
            "schema_version": raw.get("schema_version"),
            "$schema": raw.get("$schema"),
            "$id": raw.get("$id"),
            "type": raw.get("type"),
            "size_bytes": stat.st_size,
            "last_modified": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "schema": raw,
        }
        entries.append(entry)
    return entries


def _schema_files(config: ConfigModel) -> list[Path]:
    base_dir = _schema_directory_from_config(config)
    if not base_dir.exists() or not base_dir.is_dir():
        return []

    paths = [path for path in sorted(base_dir.glob("*.json")) if path.is_file()]
    return paths


def _filter_schema_entries(
    entries: list[dict[str, Any]], schema_name: str
) -> list[dict[str, Any]]:
    normalized = schema_name.lower()
    matches: list[dict[str, Any]] = []
    for entry in entries:
        name_lower = entry.get("name", "").lower()
        if not name_lower:
            continue
        if name_lower == normalized:
            matches.append(entry)
            continue
        if name_lower.endswith(f"{normalized}.schema.json"):
            matches.append(entry)
            continue
        if (
            name_lower.endswith(f"{normalized}.json")
            and "schema.json" not in normalized
        ):
            matches.append(entry)
    return matches


def _schema_metadata_issues(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for entry in entries:
        missing_fields: list[str] = []
        if not entry.get("title"):
            missing_fields.append("title")
        if not entry.get("schema_version"):
            missing_fields.append("schema_version")
        if missing_fields:
            issues.append(
                {
                    "name": entry.get("name"),
                    "path": entry.get("path"),
                    "missing_fields": missing_fields,
                    "hint": "Add the listed metadata so describe-schemas can publish stable info.",
                }
            )
    return issues


def _print_stage_summaries(stage_summaries: list[dict[str, Any]]) -> None:
    if not stage_summaries:
        return
    print("Stage summaries:")
    for stage in stage_summaries:
        stage_metrics: dict[str, object] = cast(
            dict[str, object], stage.get("metrics") or {}
        )
        metrics_details = ", ".join(
            f"{key}={value}" for key, value in stage_metrics.items()
        )
        metrics_segment = f" metrics={metrics_details}" if metrics_details else ""
        print(
            f"- {stage.get('stage', 'unknown')} status={stage.get('status', 'unknown')}"
            + metrics_segment
        )


def _print_profile_list(message: str, profiles: list[dict[str, Any]]) -> None:
    print(message)
    if not profiles:
        print("No endurance profiles available.")
        return
    for profile in profiles:
        profile_name = profile.get("name", "unknown")
        error = profile.get("error")
        if error:
            print(f"- {profile_name}: UNREADABLE — {error}")
            if profile.get("path"):
                print(f"    path: {profile['path']}")
            continue
        description = profile.get("description") or NO_DESCRIPTION
        duration = profile.get("duration_seconds")
        pass_count = profile.get("pass_count")
        force = profile.get("force")
        pattern = profile.get("write_pattern")
        path_value = profile.get("path")
        details: list[str] = []
        if duration is not None:
            details.append(f"duration={duration}s")
        if pass_count is not None:
            details.append(f"passes={pass_count}")
        if force is not None:
            details.append(f"force={force}")
        if pattern:
            details.append(f"pattern={pattern}")
        details_line = ", ".join(details)
        print(f"- {profile_name}: {description}")
        if details_line:
            print(f"    {details_line}")
        if path_value:
            print(f"    path: {path_value}")


@app.command(name="pipeline")
def pipeline(  # noqa: C901
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Endurance profile to apply during the pipeline (defaults to 'default' or combo-specified profile).",
    ),
    stage_plan: str | None = typer.Option(
        None,
        "--stages",
        "-S",
        help="Comma-separated stage names to execute, overriding the default pipeline order.",
    ),
    combo: str | None = typer.Option(
        None,
        "--combo",
        "-c",
        help="Run a named structured workload combo instead of manually specifying stages.",
    ),
    image_path: Path | None = typer.Option(
        None,
        "--image-path",
        "-i",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Image file to flash when running the image-flash stage.",
    ),
    image_block_size: str = typer.Option(
        DEFAULT_IMAGE_BLOCK_SIZE,
        "--image-block-size",
        help="Block size passed to dd when flashing the image.",
    ),
    image_conv_flags: str | None = typer.Option(
        None,
        "--image-conv-flags",
        help="Comma-separated conv flags for dd (e.g., fsync,noerror).",
    ),
    image_verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Verify the image with cmp after flashing.",
    ),
    image_write_timeout: float = typer.Option(
        600.0,
        "--image-write-timeout",
        help="Timeout in seconds for the image write step.",
    ),
    image_verify_timeout: float = typer.Option(
        300.0,
        "--image-verify-timeout",
        help="Timeout in seconds for the verification step.",
    ),
    force: bool = typer.Option(False, "--force", help=FORCE_HELP),
    dry_run: bool = typer.Option(
        False, DRY_RUN_FLAG, help="Show the pipeline plan without running any stage."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Run the default orchestration pipeline (detect → quick test → endurance)."""

    command_name = "pipeline"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        run_id = _generate_run_id()
        combo_settings: workflows_mod.WorkloadCombo | None = None
        if combo and stage_plan:
            raise ArgumentError(
                message="Pipeline cannot accept both --combo and --stages",
                details={"combo": combo, "stage_plan": stage_plan},
            )
        requested_stages: list[str] | None = None
        if stage_plan:
            requested_stages = [
                stage.strip() for stage in stage_plan.split(",") if stage.strip()
            ]
            if not requested_stages:
                raise ArgumentError(
                    message="Pipeline --stages list cannot be empty",
                    details={"raw_plan": stage_plan},
                )
        elif combo:
            combo_settings = workflows_mod.load_combo(combo, _config)
            requested_stages = list(combo_settings.stages)

        normalized_requested_stages = [
            stage.split(".")[-1].strip().lower() for stage in requested_stages or []
        ]
        needs_image_stage = any(
            stage in {"image", "image-flash"} for stage in normalized_requested_stages
        )
        image_config: pipeline_mod.ImageFlashConfig | None = None
        image_options: dict[str, object] | None = None
        if needs_image_stage:
            if image_path is None:
                raise ArgumentError(
                    message="Pipeline image-flash stage requires --image-path",
                    details={"stage_plan": stage_plan, "combo": combo},
                )
            image_config, image_options = _build_image_flash_config(
                image_path=image_path,
                cli_block_size=image_block_size,
                cli_conv_flags=image_conv_flags,
                cli_verify=image_verify,
                cli_write_timeout=image_write_timeout,
                cli_verify_timeout=image_verify_timeout,
                combo=combo_settings,
            )

        resolved_profile_name = profile or "default"
        if not profile and combo_settings and combo_settings.profile:
            resolved_profile_name = combo_settings.profile
        profile_settings = profile_mod.load_profile(resolved_profile_name, _config)

        run_ctx = RunContext(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
            device=target_device,
            config_profile=profile_settings.name,
            destructive=False,
            mode="ai" if actual_output == "json" else "human",
            log_dir=_config.log_dir,
        )

        if requested_stages:
            stages = pipeline_mod.build_pipeline(
                profile_settings, requested_stages, image_config=image_config
            )
        else:
            stages = pipeline_mod.build_default_pipeline(profile_settings)

        negotiated_stage_plan = [stage.name for stage in stages]
        # The profile can supply force just as it does for the standalone
        # endurance command, so honour both sources; --yes is still required.
        effective_force = force or bool(profile_settings.force)
        plan_writes = pipeline_mod.plan_is_destructive(negotiated_stage_plan)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device": target_device.path,
                    "stage_plan": negotiated_stage_plan,
                    "requested_stages": requested_stages,
                    "profile": profile_settings.name,
                    "combo": combo_settings.name if combo_settings else None,
                    "image_options": image_options,
                    "force": effective_force,
                    "writes_to_device": plan_writes,
                },
                actual_output,
                force=effective_force,
                check_safety=plan_writes,
            )
            return

        # Guard once for the whole plan: a read-only plan (detect/health/summary)
        # stays usable on a mounted card, anything that writes does not.
        if plan_writes:
            _assert_device_safe(ctx, target_device, effective_force)
        results = pipeline_mod.run_pipeline(run_ctx, stages)
        aggregated_status = _aggregate_pipeline_status(results)
        stage_payloads = [result.model_dump() for result in results]
        log_path = results[-1].logs_path if results else None
        stage_details = _build_stage_details(results)
        metadata = _build_pipeline_metadata(
            results=results,
            negotiated_stage_plan=negotiated_stage_plan,
            requested_stages=requested_stages,
            stage_details=stage_details,
            image_options=image_options,
            combo=combo_settings,
        )
        history_path = history_mod.record_run(
            command=command_name,
            run_id=run_id,
            device_path=target_device.path,
            status=aggregated_status,
            message=_pipeline_message(aggregated_status),
            stage_count=len(results),
            profile=profile_settings.name,
            log_path=log_path,
            metadata=metadata,
        )

        resp_data: dict[str, object] = {
            "profile": profile_settings.name,
            "stages": stage_payloads,
            "stage_count": len(stage_payloads),
            "history_path": str(history_path),
            "stage_plan": negotiated_stage_plan,
            "requested_stage_plan": requested_stages,
        }
        if image_options:
            resp_data["image_options"] = image_options
        if combo_settings:
            resp_data["combo"] = _combo_payload(combo_settings)
        resp = CLIResponse(
            status=_cli_status_from_pipeline_status(aggregated_status),
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message=_pipeline_message(aggregated_status),
            data=resp_data,
            log_path=log_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        for stage in stage_payloads:
            print(
                f"- {stage['name']} status={stage['status']} duration={stage.get('duration_seconds')}s"
            )
        print(f"History index updated at {history_path}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="workload-smallfiles")
def workload_smallfiles_command(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    file_count: int = typer.Option(
        256,
        "--file-count",
        "-n",
        help="Number of small files to touch.",
    ),
    file_size: int = typer.Option(
        1024,
        "--file-size",
        "-s",
        help="Size of each file in bytes.",
    ),
    directory: str | None = typer.Option(
        None,
        "--directory",
        "-D",
        help="Working directory for the workload.",
    ),
    delete_after: bool = typer.Option(
        True,
        "--delete-after/--no-delete",
        help="Remove files immediately after they are processed.",
    ),
    read_after_write: bool = typer.Option(
        True,
        "--read-after-write/--no-read",
        help="Read files back after writing to verify contents.",
    ),
    dry_run: bool = typer.Option(
        False,
        DRY_RUN_FLAG,
        help="Show the small-file workload plan without touching files.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Execute the small-file workload engine."""

    command_name = "workload-smallfiles"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        run_id = _generate_run_id()
        cfg = workload_smallfiles.SmallFileWorkloadConfig(
            file_count=file_count,
            file_size_bytes=file_size,
            working_dir=Path(directory) if directory else None,
            delete_after=delete_after,
            read_after_write=read_after_write,
        )
        # Same rules the engine applies, so a dry run never advertises a plan
        # the real invocation would refuse.
        workload_smallfiles.validate_config(cfg)

        if _resolve_dry_run(ctx, dry_run):
            _emit_dry_run(
                ctx,
                command_name,
                target_device,
                {
                    "device_path": target_device.path,
                    "file_count": cfg.file_count,
                    "file_size_bytes": cfg.file_size_bytes,
                    "working_directory": (
                        str(cfg.working_dir) if cfg.working_dir else None
                    ),
                    "delete_after": cfg.delete_after,
                    "read_after_write": cfg.read_after_write,
                },
                actual_output,
                label="small-file workload",
                # Writes through a mounted filesystem, so it is exempt from the
                # unmounted-device guard; previewing a refusal would mislead.
                check_safety=False,
            )
            return
        run_ctx = RunContext(
            run_id=run_id,
            started_at=datetime.now(timezone.utc),
            device=target_device,
            config_profile="workload-smallfiles",
            destructive=False,
            mode="ai" if actual_output == "json" else "human",
            log_dir=_config.log_dir,
        )

        result = workload_smallfiles.run_small_file_workload(run_ctx, cfg)
        history_mod.record_run(
            command=command_name,
            run_id=run_id,
            device_path=target_device.path,
            status=result.status,
            message="Small-file workload completed.",
            stage_count=1,
            metadata={
                "file_count": cfg.file_count,
                "file_size_bytes": cfg.file_size_bytes,
                "delete_after": cfg.delete_after,
                "read_after_write": cfg.read_after_write,
            },
            log_path=result.logs_path,
        )

        cli_status: Literal["ok", "fail", "error"] = (
            "ok" if result.status in ("ok", "warning") else "error"
        )
        resp = CLIResponse(
            status=cli_status,
            command=command_name,
            run_id=run_id,
            device={"path": target_device.path},
            message="Small-file workload completed.",
            data={"result": result.model_dump()},
            log_path=result.logs_path,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        workload_metrics: dict[str, object] = cast(dict[str, object], result.metrics)
        if workload_metrics:
            print(METRICS_LABEL)
            for key, value in workload_metrics.items():
                print(f"- {key}: {value}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="health")
def health(
    ctx: typer.Context,
    device: str = typer.Option(..., "--device", "-d", help=DEVICE_PATH_HELP),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Read device-specific CID/health registers."""

    command_name = "health"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        target_device = devices_mod.get_device(device)
        snapshot: health_snapshot.HealthSnapshot = health_snapshot.run_health_snapshot(
            target_device
        )
        data: dict[str, object] = {
            "device": target_device.model_dump(),
            "snapshot": snapshot,
        }
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=(
                f"Health snapshot captured for {target_device.path}"
                if snapshot.get("available")
                else f"No health data available for {target_device.path}"
            ),
            data=data,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        source = snapshot.get("source")
        if source:
            print(f"Source: {source}")
        cid = snapshot.get("cid") or {}
        if cid:
            # `is_card_identity` is False for any SCSI-style device, not just a
            # USB card reader, so the wording stays transport-agnostic.
            label = (
                "CID"
                if cid.get("is_card_identity")
                else "Device identity (no MMC CID available)"
            )
            print(f"{label}:")
            for key, value in cid.items():
                print(f"  {key}: {value}")
        health = snapshot.get("health") or {}
        if health:
            print("Health:")
            for key, value in health.items():
                print(f"  {key}: {value}")
        else:
            # Say so plainly. This used to print invented numbers instead.
            print("Health: no data available from this device.")
        _print_health_sources(snapshot.get("sources", {}))
        details = snapshot.get("details", {})
        _print_sdmon_details(details)

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


def _print_health_sources(sources: dict[str, Any]) -> None:
    """List which health sources answered, and why the others did not.

    Health readings used to be fabricated, so an absent source looked the same
    as a working one. Naming each source makes the gap visible.
    """

    if not sources:
        return
    print("Sources:")
    for name, status in sources.items():
        if not isinstance(status, dict):
            continue
        if status.get("available"):
            print(f"  {name}: ok")
        else:
            reason = status.get("reason") or status.get("error_code") or "unavailable"
            print(f"  {name}: unavailable — {reason}")


def _print_sdmon_details(details: dict[str, object]) -> None:
    version = details.get("sdmon_version")
    if version:
        print(f"sdmon version: {version}")
    if details.get("identity_is_not_card_cid"):
        print(
            "Note: the identity above comes from the block device (a reader or "
            "enclosure), not the card's CID register. Attach the card to an MMC "
            "host controller to read its CID."
        )


@app.command(name="status")
def status_command(
    ctx: typer.Context,
    run_id: str | None = typer.Argument(None, help="Run to report on."),
    limit: int = typer.Option(20, "--limit", help="How many runs to list."),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Report on a background run, or list recent runs."""

    command_name = "status"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)

        if run_id:
            found = runstate.read(run_id, _config.log_dir)
            data: dict[str, Any] = {"run": found.to_dict()}
            message = f"Run {run_id} is {found.state}."
        else:
            runs = runstate.list_runs(_config.log_dir, limit=limit)
            data = {"runs": [entry.to_dict() for entry in runs]}
            message = f"{len(runs)} run(s) recorded."

        resp = CLIResponse(
            status="ok", command=command_name, message=message, data=data
        )
        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        entries = [data["run"]] if run_id else data["runs"]
        for entry in entries:
            percent = entry.get("percent")
            progress = f" {percent}%" if percent is not None else ""
            print(
                f"- {entry['run_id']}  {entry['command']}  {entry['state']}{progress}"
            )
            if entry.get("phase"):
                print(f"    phase: {entry['phase']}")
            if entry.get("message"):
                print(f"    {entry['message']}")
            if entry.get("wrote_to_device") and entry["state"] != "completed":
                print("    warning: this run wrote to the device before stopping")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))


@app.command(name="cancel")
def cancel_command(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run to stop."),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Stop a background run.

    A run interrupted mid-write leaves the device partially written; the
    reported state records whether it had started writing.
    """

    command_name = "cancel"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        cancelled = runstate.cancel(run_id, _config.log_dir)

        resp = CLIResponse(
            status="ok",
            command=command_name,
            run_id=run_id,
            message=f"Run {run_id} cancelled.",
            data={"run": cancelled.to_dict()},
        )
        if actual_output == "json":
            print(resp.model_dump_json())
            return
        print(resp.message)
        if cancelled.wrote_to_device:
            print(
                "This run had already written to the device. Its contents are "
                "partial; re-flash or re-test before trusting the card."
            )

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))


@app.command(name="capabilities")
def capabilities(
    ctx: typer.Context,
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Describe available tools and feature flags."""

    command_name = "capabilities"
    try:
        actual_output = _resolve_output(ctx, output)
        _ = _ensure_config(ctx)
        caps = capabilities_mod.probe_capabilities()
        data = caps.model_dump()
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message="Capabilities probe successful.",
            data=data,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(f"Platform: {caps.platform}")
        for name, tool in caps.external_tools.items():
            availability = "available" if tool.available else "missing"
            version = f" (version {tool.version})" if tool.version else ""
            print(f" - {name}: {availability}{version}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="describe")
def describe(
    ctx: typer.Context,
    command: str = typer.Argument(..., help="Registered command name."),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Show schema information for any registered command."""

    command_name = "describe"
    try:
        actual_output = _resolve_output(ctx, output)
        _ = _ensure_config(ctx)
        metadata = _get_command_description(command)
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Command schema for '{command}'.",
            data={"describe": metadata},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(json.dumps(metadata, indent=2))

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="describe-schemas")
def describe_schemas(
    ctx: typer.Context,
    schema: str | None = typer.Option(
        None,
        "--schema",
        "-s",
        help="Schema name (with or without .schema.json) to focus on.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """List JSON schema assets and metadata for automation discovery."""

    command_name = "describe-schemas"
    try:
        actual_output = _resolve_output(ctx, output)
        config = _ensure_config(ctx)
        schema_entries = _load_schema_metadata(config)
        filtered = schema_entries
        if schema:
            filtered = _filter_schema_entries(schema_entries, schema)
            if not filtered:
                raise ArgumentError(
                    message=f"Schema not found: {schema}",
                    details={"requested": schema},
                )

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Discovered {len(filtered)} JSON schema(s).",
            data={"schemas": filtered},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        schema_dir = _schema_directory_from_config(config)
        print(f"Schema directory: {schema_dir}")
        if not filtered:
            print("No JSON schemas available.")
            return
        for entry in filtered:
            print(f"- {entry.get('name')} (size={entry.get('size_bytes')} bytes)")
            title = entry.get("title")
            if title:
                print(f"    title: {title}")
            version = entry.get("schema_version")
            if version:
                print(f"    schema_version: {version}")
            description = entry.get("description")
            if description:
                print(f"    description: {description}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="report")
def report(
    ctx: typer.Context,
    run_id: str | None = typer.Option(
        None, "--run-id", help="Optional run identifier to summarize."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Summarize metrics from a recorded pipeline run."""

    command_name = "report"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        entries = history_mod.read_history()
        selected_entry = _select_report_entry(entries, run_id)
        resolved_run_id = selected_entry.get("run_id")
        if not resolved_run_id:
            raise TFQAError("History entry missing run identifier.", "INVALID_ARGUMENT")

        log_path_value = selected_entry.get("log_path")
        actual_log_path = Path(log_path_value) if log_path_value else None
        summary = summary_mod.summarize_run(
            resolved_run_id,
            log_dir=_config.log_dir if actual_log_path is None else None,
            log_path=actual_log_path,
        )

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Run summary for {resolved_run_id}.",
            data={"summary": summary, "history_entry": selected_entry},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(f"Run ID: {summary['run_id']} status={summary['overall_status']}")
        print(
            f"Events: {summary['event_count']} start={summary.get('start')} end={summary.get('end')}"
        )
        duration_seconds = summary.get("duration_seconds")
        if duration_seconds is not None:
            print(f"Duration: {duration_seconds:.1f}s")
        history_message = selected_entry.get("message")
        if history_message:
            print(f"History note: {history_message}")
        log_path = summary.get("log_path")
        if log_path:
            print(f"Log path: {log_path}")
        _print_stage_summaries(summary.get("stage_summaries", []))

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="automation-report")
def automation_report(
    ctx: typer.Context,
    run_id: str | None = typer.Option(
        None, "--run-id", help="Run identifier to include in the automation report."
    ),
    stage: str | None = typer.Option(
        None,
        "--stage",
        "-s",
        help="Optional stage name or suffix to include in trend aggregates.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Maximum number of history entries to inspect for trends.",
    ),
    push: bool = typer.Option(
        False,
        "--push",
        help="Send the automation report JSON to configured remote endpoints.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Expose a bundled automation report featuring history, summary, and trends."""

    command_name = "automation-report"
    try:
        actual_output = _resolve_output(ctx, output)
        _config = _ensure_config(ctx)
        history_limit = limit if limit and limit > 0 else None
        entries = history_mod.read_history(limit=history_limit)
        selected_entry = _select_report_entry(entries, run_id)
        resolved_run_id, summary = _summarize_history_entry(selected_entry, _config)
        aggregated = trends_mod.aggregate_stage_metrics(entries, stage_filter=stage)

        report_payload: dict[str, Any] = {
            "history_entry": selected_entry,
            "summary": summary,
            "trends": aggregated,
        }
        remote_responses: list[dict[str, Any]] = []
        if push:
            endpoints = _load_automation_endpoints(_config)
            if not endpoints:
                raise TFQAError(
                    "Automation report push requested but no endpoints are configured.",
                    "INVALID_ARGUMENT",
                )
            remote_responses = _push_automation_report(report_payload, endpoints)
            failures = [
                resp
                for resp in remote_responses
                if not resp.get("success") and resp.get("fail_on_error")
            ]
            if failures:
                raise TFQAError(
                    "Automation report push failed for required endpoint(s).",
                    "REMOTE_PUSH_FAILED",
                    {"failures": failures},
                )

        resp_data: dict[str, Any] = {"report": report_payload}
        if remote_responses:
            resp_data["remote_push"] = remote_responses

        message = (
            f"Automation report generated for {resolved_run_id}."
            if not push
            else f"Automation report generated for {resolved_run_id} and sent to remote endpoints."
        )
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=message,
            data=resp_data,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(f"Run ID: {summary.get('run_id')} status={summary.get('overall_status')}")
        duration_seconds = summary.get("duration_seconds")
        if duration_seconds is not None:
            print(f"Duration: {duration_seconds:.1f}s")
        print(f"Entries scanned: {aggregated.get('entries_processed')}")
        stage_metrics = cast(
            dict[str, dict[str, Any]], aggregated.get("stage_metrics") or {}
        )
        for line in _stage_metric_lines(stage_metrics):
            print(line)
        if remote_responses:
            print("Remote push results:")
            for response_entry in remote_responses:
                status_value = response_entry.get("status")
                error_value = response_entry.get("error")
                print(
                    f"- {response_entry.get('endpoint')}: status={status_value} error={error_value or 'none'}"
                )

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="history")
def history(
    ctx: typer.Context,
    device: str | None = typer.Option(
        None, "--device", "-d", help="Filter history entries by device path."
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        help="Maximum number of history entries to return.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Query the recorded run history."""

    command_name = "history"
    try:
        actual_output = _resolve_output(ctx, output)
        _ = _ensure_config(ctx)
        entries = history_mod.read_history()
        if device:
            entries = [entry for entry in entries if entry.get("device_path") == device]
        if limit and limit > 0:
            entries = entries[:limit]

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message=f"Retrieved {len(entries)} history entries.",
            data={"entries": entries},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        if not entries:
            print("No history entries found.")
            return
        for entry in entries:
            timestamp = entry.get("timestamp", "unknown")
            entry_device = entry.get("device_path", "unknown")
            entry_status = entry.get("status", "unknown")
            entry_command = entry.get("command", "unknown")
            print(
                f"- {timestamp} {entry_device} status={entry_status} cmd={entry_command}"
            )

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@app.command(name="trends")
def trends(
    ctx: typer.Context,
    stage: str | None = typer.Option(
        None,
        "--stage",
        "-s",
        help="Stage name or suffix to include in the trend aggregation.",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-n",
        help="Maximum number of history entries to analyze.",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Aggregate numeric stage metrics across historical runs."""

    command_name = "trends"
    try:
        actual_output = _resolve_output(ctx, output)
        _ = _ensure_config(ctx)
        history_limit = limit if limit and limit > 0 else None
        entries = history_mod.read_history(limit=history_limit)
        aggregated = trends_mod.aggregate_stage_metrics(entries, stage_filter=stage)

        resp = CLIResponse(
            status="ok",
            command=command_name,
            message="Trend summary generated from historical runs."
            if aggregated["stage_metrics"]
            else "No trend data available for the requested filters.",
            data={
                "trends": aggregated,
                "stage_filter": aggregated["stage_filter"],
                "limit": history_limit,
            },
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        print(f"Entries scanned: {aggregated['entries_processed']}")
        stage_metrics: dict[str, dict[str, Any]] = cast(
            dict[str, dict[str, Any]], aggregated.get("stage_metrics") or {}
        )
        if not stage_metrics:
            print("No stage metrics were collected from history.")
            return
        for stage_name, stage_data in stage_metrics.items():
            occurrences = stage_data.get("occurrences")
            occurrence_segment = (
                f", runs={occurrences}" if occurrences is not None else ""
            )
            print(f"- {stage_name} (count={stage_data['count']}{occurrence_segment})")
            status_counts = stage_data.get("status_counts") or {}
            if status_counts:
                counts_segment = ", ".join(
                    f"{key}={value}" for key, value in sorted(status_counts.items())
                )
                print(f"    statuses: {counts_segment}")
            duration_info = stage_data.get("duration") or {}
            duration_count = duration_info.get("count", 0)
            duration_avg = duration_info.get("average")
            duration_last = duration_info.get("last")
            if (
                duration_count
                and duration_avg is not None
                and duration_last is not None
            ):
                print(f"    duration avg={duration_avg:.2f}s last={duration_last:.2f}s")
            averages: dict[str, float] = cast(
                dict[str, float], stage_data.get("averages") or {}
            )
            for key, value in averages.items():
                print(f"    avg {key}: {value:.2f}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@config_app.command(name="show")
def config_show(
    ctx: typer.Context,
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Dump the merged TFQA configuration."""

    command_name = CONFIG_SHOW_COMMAND_NAME
    try:
        actual_output = _resolve_output(ctx, output)
        config = _ensure_config(ctx)
        payload = config.model_dump()
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message="Merged configuration loaded.",
            data={"config": payload},
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        for key, value in payload.items():
            print(f"{key}: {value}")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


@config_app.command(name="validate")
def config_validate(
    ctx: typer.Context,
    output: str | None = typer.Option(None, "--output", "-o", help=OUTPUT_HELP),
) -> None:
    """Validate the merged TFQA configuration."""

    command_name = CONFIG_VALIDATE_COMMAND_NAME
    try:
        actual_output = _resolve_output(ctx, output)
        _ = _ensure_config(ctx)
        files = [str(p) for p in cfg_mod.find_config_files()]
        data: dict[str, object] = {"valid": True, "files": files}
        resp = CLIResponse(
            status="ok",
            command=command_name,
            message="Configuration validated successfully.",
            data=data,
        )

        if actual_output == "json":
            print(resp.model_dump_json())
            return

        print(resp.message)
        if files:
            print("Loaded configuration files:")
            for path in files:
                print(f" - {path}")
        else:
            print("No configuration files were found; defaults were used.")

    except TFQAError as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=e.message,
            error_code=e.error_code,
            data={"details": e.details},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code(e.error_code))
    except Exception as e:
        resp = CLIResponse(
            status="error",
            command=command_name,
            message=str(e),
            error_code="INTERNAL_ERROR",
            data={},
        )
        print(resp.model_dump_json())
        raise SystemExit(get_exit_code("INTERNAL_ERROR"))


if __name__ == "__main__":
    app()
