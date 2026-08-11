"""Core data models for FlashCrucible (tfqa).

All cross-module data structures are defined here using Pydantic v2.
These models provide:
  - Type safety across the codebase
  - JSON serialization for AI/automation
  - Stable schemas for CLI output
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Type aliases for clarity and semantic meaning
RunId = str  # e.g., "2025-11-18T10-15-30Z_9f3a21"
DevicePath = str  # e.g., "/dev/sdb"
TestStatus = Literal["ok", "warning", "failed", "skipped", "error"]


@dataclass(frozen=True)
class EnduranceConfig:
    """Configuration for an endurance/burn-in loop."""

    duration_seconds: float = 60.0
    pass_count: int = 5
    force: bool = False
    write_pattern: str = "sequential"

    def with_overrides(self, **overrides: Any) -> "EnduranceConfig":
        if not overrides:
            return self
        return replace(self, **overrides)


class DeviceInfo(BaseModel):
    """Block device metadata and detection information."""

    path: DevicePath  # e.g., "/dev/sdb"
    name: str  # e.g., "sdb"
    model: Optional[str] = None  # e.g., "SanDisk Ultra"
    vendor: Optional[str] = None  # e.g., "SanDisk"
    serial: Optional[str] = None
    size_bytes: int  # Total capacity in bytes
    is_removable: bool  # Detected as removable (USB/SD)
    is_system_disk: bool  # Heuristic: likely system disk (mounted at /, /boot, etc.)
    mountpoints: list[dict[str, str]] = Field(
        default_factory=list
    )  # [{mountpoint, fstype}, ...]
    transport: Optional[str] = None  # "usb", "mmc", "nvme", "ata", etc.

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "path": "/dev/sdb",
                "name": "sdb",
                "model": "SanDisk Ultra",
                "vendor": "SanDisk",
                "serial": "120619112345",
                "size_bytes": 128_000_000_000,
                "is_removable": True,
                "is_system_disk": False,
                "mountpoints": [],
                "transport": "usb",
            }
        }
    )


class RunContext(BaseModel):
    """Execution context for a test run."""

    run_id: RunId  # Unique identifier for this run
    started_at: datetime
    device: DeviceInfo
    config_profile: str = "default"  # e.g., "default", "lab-heavy"
    destructive: bool = False  # Whether this run includes destructive tests
    mode: Literal["human", "ci", "ai"] = "human"  # Output/behavior mode
    extra_tags: dict[str, str] = Field(default_factory=dict)  # Custom metadata
    log_dir: Optional[Path] = None  # ~/.tfqa/logs/run-{run_id}/


class TestConfig(BaseModel):
    """Configuration for a specific test."""

    __test__ = False

    name: str  # e.g., "capacity.quick", "performance.sequential"
    destructive: bool = False  # Does this test write to device?
    timeout_seconds: Optional[int] = None  # Override default timeout
    params: dict[str, Any] = Field(default_factory=dict)  # Test-specific options


class TestResult(BaseModel):
    """Result of a single test execution."""

    __test__ = False

    name: str  # e.g., "capacity.quick"
    status: TestStatus  # Overall outcome
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = 0.0
    metrics: dict[str, float] = Field(
        default_factory=dict
    )  # e.g., {throughput_mbps, error_count, ...}
    details: dict[str, Any] = Field(
        default_factory=dict
    )  # Arbitrary structured info specific to test
    error_code: Optional[str] = None  # e.g., "EXT_TOOL_MISSING", "DEVICE_UNSAFE"
    error_message: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    logs_path: Optional[Path] = None  # Per-test log file or run log path


class ToolCapability(BaseModel):
    """External tool availability and version information."""

    name: str  # e.g., "f3probe", "mmc", "fio", "badblocks"
    available: bool
    version: Optional[str] = None
    path: Optional[str] = None  # Absolute path to tool
    notes: Optional[str] = None


class Capabilities(BaseModel):
    """Overall platform capabilities snapshot."""

    version: str  # e.g., "0.1.0"
    platform: str  # e.g., "Linux x86_64"
    external_tools: dict[str, ToolCapability] = Field(default_factory=dict)
    features: dict[str, Literal["native", "wrapper", "hybrid", "disabled"]] = Field(
        default_factory=dict
    )  # Feature -> implementation mode


class CLIResponse(BaseModel):
    """Standard response envelope for all CLI commands with `--output json`."""

    status: Literal["ok", "fail", "error", "aborted"]
    command: str  # e.g., "quick-test", "detect", "capabilities"
    run_id: Optional[RunId] = None
    device: Optional[dict[str, str]] = None  # At minimum: {"path": "/dev/sdb"}
    error_code: Optional[str] = None  # Machine-parseable error identifier
    message: str  # Human-readable summary
    data: dict[str, Any] = Field(default_factory=dict)  # Command-specific payload
    log_path: Optional[Path] = None


class ConfigModel(BaseModel):
    """Project-level configuration (loaded from toml/env/cli)."""

    log_dir: Optional[Path] = None  # Override default ~/.tfqa/logs
    profiles_dir: Optional[Path] = None  # Override default profiles location
    schemas_dir: Optional[Path] = None  # Optional override for JSON schemas discovery
    # Add more config fields as needed (timeouts, retry policies, etc.)

    model_config = ConfigDict(extra="allow")
