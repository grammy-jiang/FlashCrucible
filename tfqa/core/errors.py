"""Unified error handling for FlashCrucible.

Defines:
  - Stable error codes for AI/automation consumption
  - Exception hierarchy
  - Mapping to exit codes and HTTP-like status meanings
"""

from __future__ import annotations

from typing import Any

# Stable error codes (machine-parseable, never change in point releases)
ERROR_CODE = {
    # Configuration & argument errors
    "INVALID_ARGUMENT": 2,  # Bad CLI args or config
    # Device errors
    "DEVICE_NOT_FOUND": 3,  # Specified device path doesn't exist
    "DEVICE_UNSAFE": 3,  # Destructive op on system/mounted device
    "NO_ROOT_PERMISSION": 3,  # Insufficient privileges
    # Tool/capability errors
    "EXT_TOOL_MISSING": 3,  # Wrapper tool not found, no fallback available
    # Runtime errors
    "RUNTIME_IO_ERROR": 1,  # I/O operation failed
    "TIMEOUT": 1,  # Operation exceeded timeout
    "INTERRUPTED": 130,  # User interruption (Ctrl-C)
    # Capability errors
    "NOT_IMPLEMENTED": 3,  # The engine cannot do real work on this build
    # Internal errors
    "INTERNAL_ERROR": 1,  # Unexpected exception
    "REMOTE_PUSH_FAILED": 1,  # Automation endpoints refused the report
}


def get_exit_code(error_code: str | None) -> int:
    """Map error_code to CLI exit code.

    Args:
        error_code: Machine-parseable error code string

    Returns:
        Unix-style exit code (0=success, >0=failure)
    """
    if not error_code:
        return 0
    return ERROR_CODE.get(error_code, 1)


# Exception hierarchy
class TFQAError(Exception):
    """Base exception for all FlashCrucible errors."""

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize TFQA error.

        Args:
            message: Human-readable error message
            error_code: Stable machine-parseable error code
            details: Additional context (dict)
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code or "INTERNAL_ERROR"
        self.details = details or {}


class ArgumentError(TFQAError):
    """CLI argument or configuration error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "INVALID_ARGUMENT", details)


class DeviceNotFoundError(TFQAError):
    """Device path does not exist."""

    def __init__(self, device_path: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            f"Device not found: {device_path}",
            "DEVICE_NOT_FOUND",
            details or {"device_path": device_path},
        )


class DeviceUnsafeError(TFQAError):
    """Destructive operation on unsafe device (system disk, mounted, etc.)."""

    def __init__(
        self,
        device_path: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        base_details: dict[str, Any] = {"device_path": device_path, "reason": reason}
        if details:
            base_details.update(details)
        super().__init__(
            f"Device unsafe for destructive operation: {reason}",
            "DEVICE_UNSAFE",
            base_details,
        )


class PermissionError(TFQAError):
    """Insufficient privileges (e.g., not root)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "NO_ROOT_PERMISSION", details)


class ToolNotFoundError(TFQAError):
    """External tool not available and no fallback."""

    def __init__(self, tool_name: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            f"Required tool not found: {tool_name}",
            "EXT_TOOL_MISSING",
            details or {"tool_name": tool_name},
        )


class NotImplementedEngineError(TFQAError):
    """The engine cannot do real work, so it refuses to report a result.

    Distinct from a missing external tool: nothing can be installed to make this
    one work. It exists so an unimplemented engine fails loudly instead of
    returning numbers it never measured.
    """

    def __init__(
        self, engine: str, reason: str, details: dict[str, Any] | None = None
    ) -> None:
        base: dict[str, Any] = {"engine": engine, "reason": reason}
        if details:
            base.update(details)
        super().__init__(
            f"{engine} is not implemented: {reason}",
            "NOT_IMPLEMENTED",
            base,
        )


class RuntimeIOError(TFQAError):
    """I/O or runtime operation failed."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, "RUNTIME_IO_ERROR", details)


class TimeoutError(TFQAError):
    """Operation exceeded timeout."""

    def __init__(
        self,
        message: str,
        timeout_seconds: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        base_details: dict[str, Any] = {"timeout_seconds": timeout_seconds}
        if details:
            base_details.update(details)
        super().__init__(message, "TIMEOUT", base_details)


class InterruptedError(TFQAError):
    """User interrupted the operation (e.g., Ctrl-C)."""

    def __init__(
        self,
        message: str = "Operation interrupted",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, "INTERRUPTED", details)
