"""Stable error codes used across the project.

Agents and callers should import these constants rather than using raw strings.
"""

from typing import TypedDict


class ErrorInfo(TypedDict):
    exit_code: int
    message: str


ERROR_CODES: dict[str, ErrorInfo] = {
    "INVALID_ARGUMENT": {"exit_code": 2, "message": "Invalid CLI arguments or config."},
    "DEVICE_NOT_FOUND": {"exit_code": 2, "message": "Device path not found."},
    "DEVICE_UNSAFE": {
        "exit_code": 3,
        "message": "Refusing destructive operation on likely system disk or mounted device.",
    },
    "NO_ROOT_PERMISSION": {
        "exit_code": 3,
        "message": "Insufficient privileges to perform operation.",
    },
    "EXT_TOOL_MISSING": {
        "exit_code": 3,
        "message": "Required external tool is missing.",
    },
    "RUNTIME_IO_ERROR": {
        "exit_code": 3,
        "message": "I/O failure during device access.",
    },
    "REMOTE_PUSH_FAILED": {
        "exit_code": 1,
        "message": "Automation report push failed for required endpoint(s).",
    },
    "INTERNAL_ERROR": {
        "exit_code": 3,
        "message": "Unexpected internal error occurred.",
    },
}


def code_info(code: str) -> ErrorInfo | None:
    return ERROR_CODES.get(code)
