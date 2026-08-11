"""An MCP server over stdio, projecting the CLI without reimplementing it.

Agents currently shell out to `tfqa` and parse stdout. This exposes the same
commands as MCP tools so they can be called natively, with typed results.

Two decisions shape the whole module:

*The tools run the real CLI.* Each call becomes an argv and a subprocess, so the
safety guard, the exit codes, and the JSON envelope come from the implementation
that is already tested. An in-process shortcut would be a second path through
the destructive commands, and the one thing that must not have two paths is the
decision to overwrite a card.

*Nothing is described twice.* Tool schemas are derived from `describe` and from
the shipped result schemas -- see `tools.py`.

The transport is newline-delimited JSON-RPC 2.0, which is what MCP stdio is. It
is implemented here rather than pulled in so that `tfqa` keeps working with no
extra dependency, and so the hermetic test job can exercise it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import IO, Any

from tfqa.mcp import tools as tool_defs

#: The MCP revision this server implements.
PROTOCOL_VERSION = "2025-06-18"

#: A tool call that never returns would wedge the server, which is
#: single-threaded by design. Long runs are meant to use `detach` and be polled
#: with `status`, so a block this long is a caller mistake worth reporting.
DEFAULT_TIMEOUT_SECONDS = 600

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _version() -> str:
    from tfqa import __version__

    return __version__


def _timeout() -> int:
    raw = os.environ.get("TFQA_MCP_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


class Server:
    """Dispatches JSON-RPC requests. Kept separate from the loop so it is testable."""

    def __init__(self, registry: dict[str, dict[str, Any]] | None = None) -> None:
        if registry is None:
            from tfqa.cli.main import _build_describe_registry

            registry = _build_describe_registry()
        self._registry = registry
        self._by_tool = {
            tool_defs.tool_name(command): metadata
            for command, metadata in registry.items()
            if command not in tool_defs.NOT_TOOLS
        }
        self._tools = tool_defs.build_tools(registry)

    # -- request handling ------------------------------------------------

    def handle(self, request: Any) -> dict[str, Any] | None:
        """Return a response, or None for a notification."""

        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 request")
        method = request.get("method")
        if not isinstance(method, str):
            return _error(request.get("id"), INVALID_REQUEST, "Missing method")

        identifier = request.get("id")
        # A notification carries no id and must draw no response, or the
        # client sees a reply to a message it is not tracking.
        if identifier is None:
            return None

        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(identifier, INVALID_PARAMS, "params must be an object")

        try:
            if method == "initialize":
                return _ok(identifier, self._initialize(params))
            if method == "ping":
                return _ok(identifier, {})
            if method == "tools/list":
                return _ok(identifier, {"tools": self._tools})
            if method == "tools/call":
                return _ok(identifier, self._call(params))
        except Exception as exc:  # pragma: no cover - defence in depth
            return _error(identifier, INTERNAL_ERROR, str(exc))
        return _error(identifier, METHOD_NOT_FOUND, f"Unknown method: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        return {
            # Echoing back a version we do not implement would be a lie the
            # client cannot check, so an unknown request gets ours.
            "protocolVersion": (
                requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION
            ),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "flashcrucible", "version": _version()},
            "instructions": (
                "Every tool is a tfqa command and returns its CLIResponse "
                "envelope, which validates against the tool's outputSchema. "
                "Destructive tools refuse unless you pass both `force` and "
                "`yes`; that refusal is the guard working, not a bug to route "
                "around. Use `dry_run` to see the plan and the safety verdict "
                "without writing. Long runs should pass `detach` and be polled "
                "with the `status` tool."
            ),
        }

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            return _tool_error("A tool call needs a name")
        metadata = self._by_tool.get(name)
        if metadata is None:
            return _tool_error(f"Unknown tool: {name}")

        arguments = params.get("arguments") or {}
        try:
            argv = tool_defs.argv_for(metadata, arguments)
        except tool_defs.ToolError as exc:
            return _tool_error(str(exc))

        completed = self._run(argv)
        if completed is None:
            return _tool_error(
                f"{name} did not finish within {_timeout()}s. Long runs should "
                "pass `detach` and be polled with the `status` tool."
            )

        payload = _parse_envelope(completed)
        if payload is None:
            return _tool_error(
                f"{name} produced no JSON envelope (exit {completed.returncode}). "
                f"stderr: {completed.stderr.strip()[:500]}"
            )
        return {
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "structuredContent": payload,
            # `fail` is a result, not a transport failure: a counterfeit card
            # detected is the tool working. Only `error` is an error.
            "isError": payload.get("status") == "error",
        }

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str] | None:
        environment = {**os.environ, "TFQA_MODE": "ai", "TFQA_NON_INTERACTIVE": "1"}
        # The run id names a state file; inheriting the server's would make
        # every tool call overwrite the same run.
        environment.pop("TFQA_RUN_ID", None)
        try:
            return subprocess.run(
                [sys.executable, "-m", "tfqa", *argv],
                capture_output=True,
                text=True,
                timeout=_timeout(),
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None


def _parse_envelope(completed: subprocess.CompletedProcess[str]) -> Any:
    try:
        return json.loads(completed.stdout)
    except ValueError:
        return None


def _ok(identifier: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def _tool_error(message: str) -> dict[str, Any]:
    """A failed call reported inside the result, as MCP expects.

    The agent, not the transport, is the one that has to act on it.
    """

    return {"content": [{"type": "text", "text": message}], "isError": True}


def serve(
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    server: Server | None = None,
) -> None:
    """Read requests until stdin closes."""

    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    dispatcher = server if server is not None else Server()

    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            response: dict[str, Any] | None = _error(
                None, PARSE_ERROR, f"Invalid JSON: {exc}"
            )
        else:
            response = dispatcher.handle(request)
        if response is None:
            continue
        sink.write(json.dumps(response) + "\n")
        sink.flush()
