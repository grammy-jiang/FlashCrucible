# Copilot Instructions for FlashCrucible

## Project Overview

**FlashCrucible** (tfqa) is a Linux-focused SD/TF card QA testing platform designed for production-scale validation. It wraps mature Linux tools (F3, mmc-utils, badblocks, fio) while providing native Python fallbacks, always prioritizing safety and AI-friendly interfaces.

**Key Identity**:

- Targets Linux x86_64, arm32, arm64 (Debian/Ubuntu/RHEL families)
- Python 3.13+, using `uv` for dependency management
- CLI-first tool with AI as a first-class citizen
- Never destructive by default; explicit confirmations mandatory
- Stable JSON schemas for automation/AI consumption

## Architecture at a Glance

FlashCrucible has 6 core architectural layers (see `docs/design-v0-structure.md` for full details):

1. **CLI & UX** (`tfqa.cli`) – Human & AI interaction
2. **Core Infrastructure** (`tfqa.core`) – Device detection, config, logging, capabilities
3. **Test Engines** (`tfqa.tests.*`) – Capacity, surface, performance, endurance tests
4. **External Tool Wrappers** (`tfqa.ext`) – F3, mmc-utils, badblocks, fio, etc.
5. **Reporting** (`tfqa.reporting`) – JSONL event aggregation, summaries
6. **Orchestration** (`tfqa.orchestration`) – Pipelines, test profiles

**Critical Pattern**: All public APIs must support both human-readable and JSON output via `--output {human,json}` flag.

## Developer Workflows & Commands

### Setup

```bash
uv install           # Bootstrap venv + install deps from pyproject.toml
uv run tfqa --help   # Test CLI entry point
```

### Testing & Quality

```bash
uv run pytest tests/           # Run all unit tests
uv run pytest tests/ -xvs      # Verbose, stop-on-first-failure
uv run ruff check tfqa/ tests/ # Lint
uv run ruff format tfqa/ tests/ # Format
uv run mypy tfqa/              # Type checking (strict on CLI/subprocess layers)
```

### Key: Non-Interactive Mode for Testing

When writing tests that simulate destructive operations, always use `--yes --non-interactive` to prevent test hangs:

```python
# In tests, mock subprocess and set env TFQA_MODE=ai
result = subprocess.run(["tfqa", "full-capacity-test", "--device", "/dev/fake", "--yes", "--non-interactive"])
```

## Project-Specific Conventions

### 1. **Safety-First Device Selection**

- **Never assume a device**: Always require explicit `--device` or indexed selection from `detect` output
- **System disk protection**: Block destructive ops on `/dev/sda`, `/boot` mounts unless forced with `--force --yes`
- **Mounted device check**: Refuse destructive tests on mounted filesystems without explicit override
- **Implementation**: See `tfqa.core.safety.assert_safe_for_destructive(device, flags)`

### 2. **Data Models & Type Boundaries**

- All cross-module data lives in `tfqa.core.models` (Pydantic v2 BaseModel)
- Key models: `DeviceInfo`, `RunContext`, `TestResult`, `TestConfig`, `ToolCapability`
- All models must serialize cleanly to JSON for AI consumption
- Use `Literal` types for enums (status: "ok" | "fail" | "error" | "aborted")

**Core Pydantic Models Structure** (`tfqa/core/models.py`):

```python
from typing import Literal, Optional, Dict, List, Any
from pydantic import BaseModel, Field
from datetime import datetime
from pathlib import Path

# Type aliases for clarity
RunId = str                    # e.g., "2025-11-18T10-15-30Z_9f3a21"
DevicePath = str              # e.g., "/dev/sdb"
TestStatus = Literal["ok", "warning", "failed", "skipped", "error"]

class DeviceInfo(BaseModel):
    """Block device metadata."""
    path: DevicePath
    name: str                  # e.g., "sdb"
    model: Optional[str] = None
    vendor: Optional[str] = None
    serial: Optional[str] = None
    size_bytes: int
    is_removable: bool
    is_system_disk: bool       # Detected via mount or heuristics
    mountpoints: List[Dict[str, str]] = Field(default_factory=list)  # [{mountpoint, fstype}, ...]
    transport: Optional[str] = None  # "usb", "mmc", "nvme", "ata"

class RunContext(BaseModel):
    """Execution context for a test run."""
    run_id: RunId
    started_at: datetime
    device: DeviceInfo
    config_profile: str = "default"  # e.g., "default", "lab-heavy"
    destructive: bool = False
    mode: Literal["human", "ci", "ai"] = "human"
    extra_tags: Dict[str, str] = Field(default_factory=dict)
    log_dir: Optional[Path] = None   # ~/.tfqa/logs/run-{run_id}.jsonl

class TestConfig(BaseModel):
    """Configuration for a specific test."""
    name: str                  # e.g., "capacity.quick", "performance.sequential"
    destructive: bool = False
    timeout_seconds: Optional[int] = None
    params: Dict[str, Any] = Field(default_factory=dict)  # Test-specific options

class TestResult(BaseModel):
    """Result of a single test execution."""
    name: str
    status: TestStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(default=0.0)
    metrics: Dict[str, float] = Field(default_factory=dict)  # e.g., {"throughput_mbps": 45.2, "error_count": 0}
    details: Dict[str, Any] = Field(default_factory=dict)   # Arbitrary structured info
    error_code: Optional[str] = None  # e.g., "EXT_TOOL_MISSING", "DEVICE_UNSAFE"
    error_message: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    logs_path: Optional[Path] = None   # Per-test or shared log file

class ToolCapability(BaseModel):
    """External tool availability info."""
    name: str                  # "f3probe", "mmc", "fio", "badblocks", etc.
    available: bool
    version: Optional[str] = None
    path: Optional[str] = None
    notes: Optional[str] = None

class Capabilities(BaseModel):
    """Overall platform capabilities snapshot."""
    version: str               # e.g., "0.1.0"
    platform: str              # "Linux x86_64"
    external_tools: Dict[str, ToolCapability]
    features: Dict[str, Literal["native", "wrapper", "hybrid", "disabled"]]

class CLIResponse(BaseModel):
    """Standard response envelope for all CLI commands with `--output json`."""
    status: Literal["ok", "fail", "error", "aborted"]
    command: str               # e.g., "quick-test", "detect", "capabilities"
    run_id: Optional[RunId] = None
    device: Optional[Dict[str, str]] = None  # At minimum: {"path": "/dev/sdb"}
    error_code: Optional[str] = None
    message: str               # Human-readable summary
    data: Dict[str, Any] = Field(default_factory=dict)  # Command-specific payload
    log_path: Optional[Path] = None
```

**Key Design Decisions**:

- `DeviceInfo` includes `is_system_disk` computed during detection (heuristic + safe defaults)
- `TestResult` tracks both structured `metrics` and arbitrary `details` for flexibility
- `CLIResponse` wraps all command outputs for consistency across human/JSON modes
- All models use `Field(default_factory=...)` to avoid mutable-default pitfalls
- `Literal` types for enums enable stable AI parsing and schema introspection

### 3. **External Tool Wrapping Pattern**

Each wrapper in `tfqa.ext` follows this structure:

```python
# tfqa/ext/f3.py - Example pattern
def run_f3probe(device_path: str) -> dict:
    """Wrapper: Execute f3probe, parse output, return structured dict."""
    # Check capability first: tfqa.core.capabilities.check_tool("f3probe")
    # Run process, capture stdout/stderr, timeout safety
    # Parse output into native Python structures
    # Return: {"real_size_bytes": X, "fake_detected": True/False, ...}

# If tool missing, fallback to native impl or raise ToolUnavailableError
```

### 4. **Test Engine Interface**

All test implementations in `tfqa.tests.*` follow:

```python
async def run_test(ctx: RunContext, config: TestConfig) -> TestResult:
    """
    Inputs: RunContext (device, run_id, logging), TestConfig (params)
    Output: TestResult (name, status, metrics, logs_path, error_code)
    Side effects: Write to ctx.logger (JSONL), emit events
    """
```

### 5. **Logging & JSONL Event Stream**

- All test execution must emit structured JSONL events to `~/.tfqa/logs/run-{run_id}.jsonl`
- Fields: `timestamp`, `run_id`, `device`, `phase`, `event_type`, `message`, `metrics`
- Never mix human text and JSON in same stream
- Use `tfqa.core.logging` for centralized event emission

### 6. **CLI Subcommand Patterns**

- Use Typer (or Click 8.x) for CLI framework
- Each subcommand in `tfqa.cli.*` maps to one file: `detect.py`, `quick_test.py`, etc.
- Subcommands must support:
  - `--output json` (stable schema)
  - `--non-interactive` (no prompts)
  - `--yes` (skip confirmations)
  - `--dry-run` (show what would happen, no I/O)
- Response envelope: `{"status", "command", "run_id", "device", "error_code", "message", "data", "log_path"}`

### 7. **Configuration Precedence**

```
CLI args > ENV vars > ./tfqa.toml > ~/.config/tfqa/config.toml > /etc/tfqa/config.toml > defaults
```

Use `pydantic.BaseModel` with validators for config objects. Support TOML (v0 standard).

### 8. **Error Codes for AI**

Stable, machine-parseable error codes that AI can decide to retry or escalate:

- `INVALID_ARGUMENT` – bad CLI args
- `DEVICE_NOT_FOUND` – device path doesn't exist
- `DEVICE_UNSAFE` – system disk or mounted without override
- `NO_ROOT_PERMISSION` – insufficient privileges
- `EXT_TOOL_MISSING` – wrapper dependency not found
- `RUNTIME_IO_ERROR` – underlying I/O failure
- `INTERNAL_ERROR` – unexpected exception

Exit codes: 0 (success), 1 (test failed), 2 (config error), 3 (environment error), 130 (interrupted).

## Implementation Sequencing (Phases)

- **Phase 0** (Skeleton & Safety): Device detection, safety guardrails, config, logging, capabilities, basic CLI UX
- **Phase 1** (Core QA): Quick/full capacity tests, health metadata, reporting, JSON schemas
- **Phase 2** (Surface & Perf): Badblocks wrapper, sequential/random I/O benchmarks, industrial health (sdmon)
- **Phase 3** (Endurance & Workload): Burn-in, small-file stress, image write/verify, orchestration profiles
- **Phase 4+** (Analytics): Trend analysis, advanced reporting, Web UI (optional)

## AI-Specific Interface Design

FlashCrucible must be **self-discoverable** by AI agents:

1. **`tfqa capabilities --output json`** – Lists all available features, tools, and their implementation mode (native/wrapper/disabled)
2. **`tfqa describe <cmd> --output json`** – Outputs command schema (args, options, destructive flag, requires_root)
3. **`--output json` on all commands** – Stable schemas for parsing
4. **`TFQA_MODE=ai`** – Environment variable shortcut for `--output json --non-interactive --no-color`

**Recommended AI Flow**:

1. Call `capabilities` to discover what's available
2. Call `describe` for each command you plan to use
3. Run operations with `--output json --non-interactive`, parse response envelope
4. Check `error_code` and `status` fields to decide retry/escalate/abort strategy

### Example JSON Responses

#### `detect --output json`

```json
{
  "status": "ok",
  "command": "detect",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "3 block devices detected.",
  "data": {
    "devices": [
      {
        "id": "dev1",
        "path": "/dev/sda",
        "size_bytes": 512110190592,
        "model": "Samsung SSD 870",
        "vendor": "Samsung",
        "removable": false,
        "is_system_disk": true,
        "mountpoints": [
          { "mountpoint": "/", "fstype": "ext4" },
          { "mountpoint": "/boot", "fstype": "vfat" }
        ],
        "transport": "ata"
      },
      {
        "id": "dev2",
        "path": "/dev/sdb",
        "size_bytes": 128035676160,
        "model": "SanDisk Ultra",
        "vendor": "SanDisk",
        "removable": true,
        "is_system_disk": false,
        "mountpoints": [],
        "transport": "usb"
      }
    ]
  },
  "log_path": null
}
```

#### `quick-test --output json` (Success)

```json
{
  "status": "ok",
  "command": "quick-test",
  "run_id": "2025-11-18T10-15-30Z_9f3a21",
  "device": { "path": "/dev/sdb" },
  "error_code": null,
  "message": "Quick capacity test completed successfully.",
  "data": {
    "ext_tool_used": "f3probe",
    "fake_capacity_detected": false,
    "estimated_real_size_bytes": 128035676160,
    "test_size_bytes": 115261392896,
    "coverage_percent": 90.1,
    "duration_seconds": 432.5,
    "throughput_mbps": 265.3
  },
  "log_path": "/home/user/.tfqa/logs/run-2025-11-18T10-15-30Z_9f3a21.jsonl"
}
```

#### `quick-test --output json` (Fake Capacity Detected)

```json
{
  "status": "fail",
  "command": "quick-test",
  "run_id": "2025-11-18T10-18-45Z_xyz555",
  "device": { "path": "/dev/sdb" },
  "error_code": null,
  "message": "Card reports 128GB but only 16GB detected. Likely counterfeit.",
  "data": {
    "ext_tool_used": "f3probe",
    "fake_capacity_detected": true,
    "reported_size_bytes": 128035676160,
    "estimated_real_size_bytes": 15931539456,
    "test_size_bytes": 15931539456,
    "coverage_percent": 92.3,
    "duration_seconds": 215.0,
    "throughput_mbps": 73.9,
    "first_error_sector": 31395841
  },
  "log_path": "/home/user/.tfqa/logs/run-2025-11-18T10-18-45Z_xyz555.jsonl"
}
```

#### `full-capacity-test --output json` (Safety Check)

```json
{
  "status": "error",
  "command": "full-capacity-test",
  "run_id": null,
  "device": { "path": "/dev/sda" },
  "error_code": "DEVICE_UNSAFE",
  "message": "Refusing destructive test on likely system disk /dev/sda.",
  "data": {
    "is_system_disk": true,
    "mounted": [
      { "mountpoint": "/", "fstype": "ext4" },
      { "mountpoint": "/boot", "fstype": "vfat" }
    ],
    "hint": "Use --force --yes --non-interactive to override (expert mode only)"
  },
  "log_path": null
}
```

#### `health --output json`

```json
{
  "status": "ok",
  "command": "health",
  "run_id": "2025-11-18T10-20-15Z_hlth01",
  "device": { "path": "/dev/mmcblk0" },
  "error_code": null,
  "message": "Health snapshot retrieved successfully.",
  "data": {
    "source": "mmc-utils+sdmon",
    "cid": {
      "manufacturer_id": 0x000002,
      "product_name": "MB-MJ64",
      "product_revision": "11",
      "serial_number": "0x1234abcd",
      "manufacture_date": "2025-01-15"
    },
    "health": {
      "life_used_percent": 3,
      "power_on_count": 21,
      "read_error_count": 0,
      "write_error_count": 0,
      "temperature_celsius": 32
    }
  },
  "log_path": "/home/user/.tfqa/logs/run-2025-11-18T10-20-15Z_hlth01.jsonl"
}
```

#### `capabilities --output json`

```json
{
  "status": "ok",
  "command": "capabilities",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "Capabilities probe successful.",
  "data": {
    "version": "0.1.0",
    "platform": "Linux x86_64",
    "features": {
      "capacity_quick": "hybrid",
      "capacity_full": "hybrid",
      "health_basic": "wrapper",
      "health_industrial": "wrapper",
      "surface_scan": "wrapper",
      "performance_seq": "native",
      "performance_random": "disabled",
      "endurance": "disabled"
    },
    "ext_tools": {
      "f3probe": {
        "found": true,
        "path": "/usr/bin/f3probe",
        "version": "8.0"
      },
      "f3write": {
        "found": true,
        "path": "/usr/bin/f3write",
        "version": "8.0"
      },
      "mmc": { "found": true, "path": "/usr/bin/mmc", "version": "0.1" },
      "sdmon": { "found": false, "path": null, "version": null },
      "badblocks": {
        "found": true,
        "path": "/sbin/badblocks",
        "version": "1.46.2"
      },
      "fio": { "found": false, "path": null, "version": null }
    }
  },
  "log_path": null
}
```

#### `describe quick-test --output json`

```json
{
  "status": "ok",
  "command": "describe",
  "run_id": null,
  "device": null,
  "error_code": null,
  "message": "Command schema for 'quick-test'.",
  "data": {
    "name": "quick-test",
    "summary": "Fast capacity/authenticity check using F3 or native sampling. Non-destructive by default.",
    "destructive": false,
    "requires_root": false,
    "arguments": [
      {
        "name": "device",
        "type": "string",
        "required": true,
        "position": 1,
        "description": "Block device path, e.g., /dev/sdb"
      }
    ],
    "options": [
      {
        "name": "--output",
        "type": "string",
        "required": false,
        "default": "human",
        "allowed_values": ["human", "json"],
        "description": "Output format."
      },
      {
        "name": "--non-interactive",
        "type": "bool",
        "required": false,
        "default": false,
        "description": "Disable all prompts."
      },
      {
        "name": "--dry-run",
        "type": "bool",
        "required": false,
        "default": false,
        "description": "Show what would be tested without executing."
      }
    ]
  },
  "log_path": null
}
```

## Key Files & Patterns

| Path                           | Purpose                                 |
| ------------------------------ | --------------------------------------- |
| `tfqa/cli/main.py`             | Typer app, subcommand dispatch          |
| `tfqa/core/models.py`          | All cross-module Pydantic models        |
| `tfqa/core/safety.py`          | Device safety checks                    |
| `tfqa/core/logging.py`         | JSONL event emission                    |
| `tfqa/core/capabilities.py`    | Tool availability probing               |
| `tfqa/ext/*.py`                | Tool wrappers (f3, mmc, badblocks, fio) |
| `tfqa/tests/capacity/quick.py` | Quick capacity/authenticity test        |
| `tfqa/tests/capacity/full.py`  | Destructive full-span test              |
| `docs/design-v0-structure.md`  | Full architecture blueprint             |
| `docs/design-v0-details.md`    | Detailed API sketches                   |
| `docs/ux-v0.md`                | CLI UX & AI interaction patterns        |
| `docs/features-roadmap-v0.md`  | Feature priorities & phasing            |

### Module Organization Details

#### `tfqa/cli/` – CLI Layer

- `main.py` – Typer app root, subcommand registration
- `detect.py` – `detect` command: list/inspect block devices
- `quick_test.py` – `quick-test` command: fast capacity/authenticity
- `full_capacity.py` – `full-capacity-test` command: destructive write+verify
- `health.py` – `health` command: CID/CSD/health metadata
- `report.py` – `report` command: aggregate results from run JSONL
- `capabilities.py` – `capabilities` command: feature/tool discovery
- `describe.py` – `describe` command: command schema introspection
- `config_cmd.py` – `config show` / `config validate` commands
- `utils.py` – Shared CLI helpers (formatting, error display, etc.)

**Pattern**: Each command file exports a single Typer command (or subgroup). All output goes through `CLIResponse` envelope with consistent `status`, `error_code`, `message`, `data` structure.

#### `tfqa/core/` – Core Infrastructure

- `models.py` – Pydantic v2 data models (see section 2 above)
- `devices.py` – Device detection via `/sys/block`, `lsblk`, `psutil`
  - Exports: `discover_devices() -> List[DeviceInfo]`, `get_device(path) -> DeviceInfo`
- `safety.py` – Device safety checks for destructive ops
  - Exports: `is_system_disk(device) -> bool`, `assert_safe_for_destructive(device, flags) -> None`
- `config.py` – Configuration loading from CLI args, env, config files
  - Exports: `load_config(args, env, config_files) -> ConfigModel`
- `logging.py` – JSONL event emission and log management
  - Exports: `create_logger(run_id) -> Logger`, `emit_event(event_dict) -> None`
- `capabilities.py` – External tool probing and capability caching
  - Exports: `probe_capabilities() -> Capabilities`, `check_tool(name) -> ToolCapability`
- `errors.py` – Unified exception hierarchy and error code definitions
  - Exports: `TFQAError` base, `DeviceUnsafeError`, `ToolNotFoundError`, etc.
- `utils.py` – Shared utilities (path helpers, subprocess wrappers, etc.)

**Pattern**: Core modules export simple, typed functions. No CLI awareness. All I/O goes through logging system.

#### `tfqa/ext/` – External Tool Wrappers

- `f3.py` – F3 suite wrapper (`f3probe`, `f3write`, `f3read`, `f3fix`)
  - Exports: `run_f3probe(device_path, free_space_only) -> dict`, `run_f3write(device_path, region) -> dict`, etc.
- `mmc.py` – mmc-utils wrapper (CID/CSD/EXT_CSD reads)
  - Exports: `read_cid(device_path) -> dict`, `read_csd(device_path) -> dict`, `read_ext_csd(device_path) -> dict`
- `sdmon.py` – sdmon wrapper (industrial card health)
  - Exports: `read_health(device_path) -> dict` or raises `ToolNotFoundError`
- `badblocks.py` – badblocks wrapper (surface scan)
  - Exports: `run_badblocks_readonly(device_path) -> dict`, `run_badblocks_write(device_path) -> dict`
- `fio.py` – fio wrapper (I/O benchmarking)
  - Exports: `run_fio_job(device_path, job_config) -> dict` (parses JSON output)
- `dd.py` – dd wrapper (image write/verify)
- `fsck.py` – fsck wrapper (filesystem checks)

**Pattern**: Each wrapper follows the same structure:

1. Check tool availability first via `tfqa.core.capabilities.check_tool(name)`
2. Build command, set timeout, capture I/O
3. Parse output into structured Python dict
4. On tool-not-found: raise `ToolNotFoundError` (caller decides fallback or fail)

#### `tfqa/tests/` – Test Engines

- `capacity/quick.py` – Quick test engine
  - Exports: `async def run_quick_capacity(ctx: RunContext, config: TestConfig) -> TestResult`
  - Uses F3 if available, falls back to native sampling
- `capacity/full.py` – Destructive full test engine
  - Exports: `async def run_full_capacity(ctx: RunContext, config: TestConfig) -> TestResult`
- `surface/scan.py` – Surface integrity scan
- `performance/basic.py` – Sequential I/O benchmarks
- `performance/random.py` – Random I/O benchmarks (fio)
- `endurance/simple.py` – Burn-in loop (future)
- `health/snapshot.py` – Health data aggregation
- `fs/smallfiles.py` – Small-file workload (Phase 3+)

**Pattern**: All test engines export an async function with signature:

```python
async def run_<test_name>(ctx: RunContext, config: TestConfig) -> TestResult
```

- Input: `RunContext` (device, run_id, logging), `TestConfig` (test-specific params)
- Output: `TestResult` (status, metrics, error_code, logs_path)
- Side effects: Emit JSONL events to `ctx.log_dir`

#### `tfqa/reporting/` – Reporting Layer

- `summary.py` – Aggregate JSONL → Summary
  - Exports: `summarize_run(run_id) -> RunSummary`
- `history.py` – Maintain run history index (optional SQLite/JSON)
- `formatters.py` – Format results for human/JSON output

#### `tfqa/orchestration/` – Orchestration Layer

- `pipeline.py` – Execute sequences of tests
  - Exports: `run_pipeline(device, profile) -> PipelineResult`
- `profiles.py` – Load/validate test profiles (TOML/YAML)

#### `tests/` – Unit Tests

- `test_cli_*.py` – CLI command tests (mock subprocesses, mock fs)
- `test_core_*.py` – Core logic tests (device detection, safety, config)
- `test_ext_*.py` – Wrapper tests (mock tool outputs, parse correctness)
- `test_tests_*.py` – Test engine tests (verify metrics, result structure)

## Common Pitfalls to Avoid

- ❌ Silent fallbacks or "magic" behavior that AI can't introspect
- ❌ Mixing human-readable logs with JSON in the same stream
- ❌ Defaults that run destructive operations
- ❌ Prompts/confirmations that hang in non-interactive mode (always check `--yes` + `--non-interactive`)
- ❌ Inconsistent error handling across wrappers
- ❌ Assuming tool availability without capabilities check
- ❌ Per-device special casing (e.g., vendor-specific quirks should be configurable, not hardcoded)

## Testing Patterns: Wrapper vs. Native Implementations

### Wrapper Testing Pattern (`tfqa/ext/*`)

**Design Principle**: Wrappers are thin adapters around external tools. They should be easy to mock and verify separately from the tools themselves.

**Example: Testing F3 Wrapper**

```python
# tests/test_ext_f3.py
import pytest
from unittest.mock import patch, MagicMock
from tfqa.ext.f3 import run_f3probe
from tfqa.core.errors import ToolNotFoundError

@pytest.mark.asyncio
async def test_f3probe_success(monkeypatch):
    """Test successful f3probe execution and output parsing."""
    mock_output = """F3 1.8 by Digirati

    [...output omitted...]
    Real capacity: 121,670.656 MB (0x7482260 sectors)
    Fake capacity: NO
    """

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=mock_output,
            stderr=""
        )

        result = await run_f3probe("/dev/sdb")

        assert result["fake_detected"] is False
        assert result["real_size_bytes"] == 121670656 * 1024
        mock_run.assert_called_once()

@pytest.mark.asyncio
async def test_f3probe_tool_not_found():
    """Test graceful failure when f3probe is missing."""
    with patch("shutil.which", return_value=None):
        with pytest.raises(ToolNotFoundError):
            await run_f3probe("/dev/sdb")

@pytest.mark.asyncio
async def test_f3probe_timeout():
    """Test timeout handling for stuck processes."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("f3probe", 60)):
        with pytest.raises(RuntimeIOError):
            await run_f3probe("/dev/sdb", timeout=60)

@pytest.mark.asyncio
async def test_f3probe_parsing_robustness():
    """Test parser robustness against malformed output."""
    malformed_outputs = [
        "F3 1.8 by Digirati\n\nNo output\n",  # Missing capacity line
        "garbage\ninvalid format",
        "",  # Empty output
    ]

    for output in malformed_outputs:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")

            with pytest.raises(ValueError, match="Could not parse"):
                await run_f3probe("/dev/sdb")
```

**Key Wrapper Testing Principles**:

1. **Mock subprocess entirely** – Don't call actual tools in unit tests
2. **Test output parsing separately** – Verify parser robustness against real tool variations
3. **Test tool-not-found path** – Ensure graceful degradation when external tool missing
4. **Test error conditions** – Timeouts, permission errors, corrupted output
5. **Capture and verify I/O** – Check that subprocess was called with correct args/env

### Native Implementation Testing Pattern (`tfqa/tests/*`)

**Design Principle**: Native implementations should be self-contained and deterministic. Mock I/O operations but test algorithm correctness.

**Example: Testing Quick Capacity (Native Sampling)**

```python
# tests/test_tests_capacity_quick.py
import pytest
from datetime import datetime
from tfqa.core.models import RunContext, TestConfig, DeviceInfo
from tfqa.tests.capacity.quick import run_quick_capacity

@pytest.fixture
def mock_run_context():
    """Fixture: minimal RunContext for testing."""
    device = DeviceInfo(
        path="/dev/fake",
        name="fake",
        size_bytes=128_000_000_000,
        is_removable=True,
        is_system_disk=False
    )
    return RunContext(
        run_id="test-run-123",
        started_at=datetime.now(),
        device=device,
        mode="ai"
    )

@pytest.mark.asyncio
async def test_quick_capacity_with_f3probe(mock_run_context, monkeypatch):
    """Test quick capacity when F3 is available."""

    # Mock capabilities check
    mock_capabilities = MagicMock()
    mock_capabilities.external_tools["f3probe"].available = True
    monkeypatch.setattr("tfqa.tests.capacity.quick.check_capabilities", lambda: mock_capabilities)

    # Mock f3probe wrapper
    mock_f3_result = {
        "fake_detected": False,
        "real_size_bytes": 128_000_000_000,
        "throughput_mbps": 265.0
    }
    monkeypatch.setattr("tfqa.tests.capacity.quick.run_f3probe",
                       return_value=mock_f3_result)

    config = TestConfig(name="capacity.quick", destructive=False)
    result = await run_quick_capacity(mock_run_context, config)

    assert result.status == "ok"
    assert result.metrics["throughput_mbps"] == 265.0
    assert result.details["fake_detected"] is False

@pytest.mark.asyncio
async def test_quick_capacity_native_fallback(mock_run_context, monkeypatch):
    """Test quick capacity using native sampling when F3 unavailable."""

    # Mock capabilities: F3 not available
    mock_capabilities = MagicMock()
    mock_capabilities.external_tools["f3probe"].available = False
    monkeypatch.setattr("tfqa.tests.capacity.quick.check_capabilities", lambda: mock_capabilities)

    # Mock block device I/O
    def mock_device_read(device_path, offset, size):
        """Simulate successful reads."""
        return b"X" * size

    monkeypatch.setattr("tfqa.tests.capacity.quick._read_block", mock_device_read)

    config = TestConfig(name="capacity.quick", destructive=False, params={"sample_points": 10})
    result = await run_quick_capacity(mock_run_context, config)

    assert result.status == "ok"
    assert result.details["method"] == "native_sampling"
    assert result.details["sample_count"] == 10

@pytest.mark.asyncio
async def test_quick_capacity_detects_fake_card(mock_run_context, monkeypatch):
    """Test fake capacity detection."""

    # Device reports 128GB but actual capacity is only 16GB
    device = mock_run_context.device
    device.size_bytes = 128_000_000_000  # Reported size

    mock_f3_result = {
        "fake_detected": True,
        "real_size_bytes": 16_000_000_000,  # Actual size
        "throughput_mbps": 73.9
    }
    monkeypatch.setattr("tfqa.tests.capacity.quick.run_f3probe",
                       return_value=mock_f3_result)

    config = TestConfig(name="capacity.quick", destructive=False)
    result = await run_quick_capacity(mock_run_context, config)

    assert result.status == "fail"  # Test failed (fake detected)
    assert result.error_code is None  # But no error (expected failure)
    assert result.details["fake_detected"] is True
    assert "counterfeit" in result.details["recommendation"].lower()

@pytest.mark.asyncio
async def test_quick_capacity_handles_io_errors(mock_run_context, monkeypatch):
    """Test error handling for I/O failures."""

    monkeypatch.setattr("tfqa.tests.capacity.quick.run_f3probe",
                       side_effect=IOError("Device I/O error"))

    config = TestConfig(name="capacity.quick")
    result = await run_quick_capacity(mock_run_context, config)

    assert result.status == "error"
    assert result.error_code == "RUNTIME_IO_ERROR"
    assert "I/O error" in result.error_message.lower()
```

**Key Native Implementation Testing Principles**:

1. **Mock external dependencies** – Use monkeypatch for wrapper calls, I/O operations
2. **Parameterize test cases** – Test multiple success paths, fallback paths, error paths
3. **Verify algorithm correctness** – Check metrics calculation, decision logic
4. **Test error cases comprehensively** – I/O errors, timeouts, invalid states
5. **Use fixtures for reusable test data** – RunContext, TestConfig, DeviceInfo, etc.
6. **Verify side effects** – Check that JSONL events were emitted correctly

### CLI Command Testing Pattern (`tfqa.cli.*`)

**Design Principle**: CLI tests should mock both core logic and external tools. Verify response envelope and exit codes.

```python
# tests/test_cli_quick_test.py
import pytest
from typer.testing import CliRunner
from tfqa.cli.main import app
from tfqa.core.models import CLIResponse

runner = CliRunner()

def test_quick_test_success(monkeypatch):
    """Test successful quick-test CLI invocation."""

    mock_result = {
        "status": "ok",
        "fake_detected": False,
        "estimated_real_size_bytes": 128_000_000_000,
        "coverage_percent": 90.1,
        "duration_seconds": 432.5,
        "throughput_mbps": 265.3
    }
    monkeypatch.setattr("tfqa.tests.capacity.quick.run_quick_capacity",
                       return_value=mock_result)

    result = runner.invoke(app, ["quick-test", "--device", "/dev/sdb", "--output", "json", "--non-interactive"])

    assert result.exit_code == 0
    response = CLIResponse.model_validate_json(result.stdout)
    assert response.status == "ok"
    assert response.command == "quick-test"
    assert response.device["path"] == "/dev/sdb"
    assert response.data["fake_detected"] is False

def test_quick_test_safety_check(monkeypatch):
    """Test that destructive test refuses system disk."""

    # Mock device detection to return system disk
    mock_device = MagicMock()
    mock_device.path = "/dev/sda"
    mock_device.is_system_disk = True

    monkeypatch.setattr("tfqa.core.devices.get_device", return_value=mock_device)

    result = runner.invoke(app, ["full-capacity-test", "--device", "/dev/sda", "--output", "json"])

    assert result.exit_code == 3  # Environment error
    response = CLIResponse.model_validate_json(result.stdout)
    assert response.status == "error"
    assert response.error_code == "DEVICE_UNSAFE"

def test_quick_test_json_output():
    """Test JSON output format compliance."""

    result = runner.invoke(app, ["detect", "--output", "json"])

    # Must be valid JSON with expected envelope fields
    response = CLIResponse.model_validate_json(result.stdout)
    assert hasattr(response, "status")
    assert hasattr(response, "command")
    assert hasattr(response, "message")
    assert hasattr(response, "data")
    assert hasattr(response, "error_code")

def test_quick_test_human_output_no_json_contamination():
    """Verify human output doesn't contain JSON."""

    result = runner.invoke(app, ["detect", "--output", "human"])

    # Should not be parseable as JSON (tables, text, no envelope)
    try:
        CLIResponse.model_validate_json(result.stdout)
        pytest.fail("Human output should not be valid JSON")
    except:
        pass  # Expected
```

**Key CLI Testing Principles**:

1. **Use CliRunner** – Invoke CLI commands in isolated test environment
2. **Mock both layers** – Mock core logic AND external tools
3. **Verify response envelope** – Check status, error_code, message, data fields
4. **Test output modes** – Verify `--output json` and `--output human` separately
5. **Test safety checks** – Verify device safety is enforced before test execution
6. **Test exit codes** – Verify correct exit code (0=success, 1=failed, 2=config err, 3=env err, 130=interrupted)

## Feedback & Iteration

This file is intentionally concise. If you find gaps or have questions about:

- Architecture decisions or trade-offs
- Specific module responsibilities
- Test organization
- Integration with external tools

Refer to the detailed design docs in `docs/`, or open an issue for clarification.
