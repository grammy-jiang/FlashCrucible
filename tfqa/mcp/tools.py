"""MCP tool descriptors, derived from the CLI rather than written a second time.

Every fact an agent needs about a tool -- its arguments, whether it is
destructive, the shape of its result -- already exists: `describe` generates the
argument metadata from the click command tree, and each command ships a result
schema. Restating any of it here would create a second copy that drifts, and a
drifted tool description is worse than none because an agent acts on it.

So this module is a projection. Adding a CLI option adds an MCP parameter with
no change here, and a command with no result schema is a build error rather
than an untyped tool.
"""

from __future__ import annotations

import json
from typing import Any

from tfqa.core import paths

#: Not exposed as tools. `config` is a group with no callback of its own, and
#: `mcp-server` would spawn a nested server reading the child's stdin.
NOT_TOOLS = frozenset({"config", "mcp-server"})

#: Options the server controls. `output` is always json -- an agent parsing the
#: human format is the failure this whole interface exists to remove.
CONTROLLED_OPTIONS = frozenset({"output", "help"})

#: click type names mapped onto JSON Schema. Unknown types are an error rather
#: than a guess: silently calling something a string is how a wrong tool
#: description gets shipped.
JSON_TYPES = {
    "text": "string",
    "string": "string",
    "path": "string",
    "file": "string",
    "filename": "string",
    "choice": "string",
    "integer": "integer",
    "integer range": "integer",
    "float": "number",
    "float range": "number",
    "boolean": "boolean",
}

_CONFIRMATION_HELP = (
    "Confirm a destructive operation. Required alongside `force`; neither one "
    "alone is enough. The server never supplies either."
)


class ToolError(ValueError):
    """A tool call that cannot be turned into a command line."""


def _json_type(descriptor: dict[str, Any]) -> str:
    name = descriptor.get("type") or "string"
    try:
        return JSON_TYPES[name]
    except KeyError as exc:  # pragma: no cover - guards a new click type
        raise ToolError(
            f"No JSON Schema type for click type {name!r}; add it to JSON_TYPES"
        ) from exc


def _parameter_schema(descriptor: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": _json_type(descriptor)}
    if descriptor.get("description"):
        schema["description"] = descriptor["description"]
    if descriptor.get("allowed_values"):
        schema["enum"] = list(descriptor["allowed_values"])
    default = descriptor.get("default")
    if default is not None:
        schema["default"] = default
    return schema


def _needs_synthetic_yes(metadata: dict[str, Any]) -> bool:
    """Whether `yes` has to map to the global flag instead of a local one.

    Most destructive commands take `--force` locally but read confirmation from
    the global `--yes`, so without this an agent could pass `force` and get a
    refusal it had no documented way to satisfy.
    """

    names = {option["name"] for option in metadata["options"]}
    return "yes" not in names and (
        "force" in names or bool(metadata.get("destructive"))
    )


def input_schema(metadata: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for descriptor in metadata["arguments"]:
        properties[descriptor["name"]] = _parameter_schema(descriptor)
        if descriptor.get("required"):
            required.append(descriptor["name"])

    for descriptor in metadata["options"]:
        if descriptor["name"] in CONTROLLED_OPTIONS:
            continue
        properties[descriptor["name"]] = _parameter_schema(descriptor)
        if descriptor.get("required"):
            required.append(descriptor["name"])

    if _needs_synthetic_yes(metadata):
        properties["yes"] = {"type": "boolean", "description": _CONFIRMATION_HELP}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        # Unknown keys are rejected rather than dropped: a caller that
        # misspells `force` must be told, not quietly given a safer run than
        # it asked for -- or a more dangerous one.
        "additionalProperties": False,
    }


def output_schema(metadata: dict[str, Any]) -> dict[str, Any]:
    name = metadata.get("result_schema")
    if not name:  # pragma: no cover - the invariant tests forbid this
        raise ToolError(f"{metadata['name']} ships no result schema")
    return json.loads((paths.DEFAULT_SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def _annotations(metadata: dict[str, Any]) -> dict[str, Any]:
    destructive = bool(metadata.get("destructive"))
    return {
        "title": metadata["name"],
        "readOnlyHint": not destructive,
        "destructiveHint": destructive,
        "idempotentHint": not destructive,
        "openWorldHint": False,
    }


def _description(metadata: dict[str, Any]) -> str:
    parts = [metadata.get("summary") or metadata["name"]]
    if metadata.get("destructive"):
        when = metadata.get("destructive_when") or "always"
        parts.append(
            f"DESTRUCTIVE ({when}). Overwriting a device requires both `force` "
            "and `yes`; the server supplies neither on your behalf."
        )
    if metadata.get("requires_root"):
        parts.append("Needs root for raw device access.")
    if metadata.get("required_tools"):
        parts.append(f"Requires: {', '.join(metadata['required_tools'])}.")
    if metadata.get("degradation"):
        parts.append(str(metadata["degradation"]))
    return " ".join(parts)


def tool_name(command: str) -> str:
    return command.replace(" ", "-")


def build_tools(registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One MCP tool per CLI command, in a stable order."""

    return [
        {
            "name": tool_name(command),
            "description": _description(metadata),
            "inputSchema": input_schema(metadata),
            "outputSchema": output_schema(metadata),
            "annotations": _annotations(metadata),
        }
        for command, metadata in sorted(registry.items())
        if command not in NOT_TOOLS
    ]


def _long(flags: list[str], name: str) -> str:
    """The long flag, which is the stable one; short flags come and go."""

    long_flags = [flag for flag in flags if flag.startswith("--")]
    if not long_flags:  # pragma: no cover - every option declares one
        raise ToolError(f"{name} has no long flag")
    return long_flags[0]


def _flag(descriptor: dict[str, Any]) -> str:
    return _long(descriptor.get("flags") or [], descriptor["name"])


def _negative_flag(descriptor: dict[str, Any]) -> str | None:
    """The off switch of a paired flag, if it has one.

    `--free-space-only/--no-free-space-only` defaults to on, so treating
    `false` as "omit the flag" left the default in force: an agent asking for a
    whole-device probe silently got the free-space one and was told it had run
    what it asked for.
    """

    secondary = descriptor.get("secondary_flags") or []
    return _long(secondary, descriptor["name"]) if secondary else None


def _render(descriptor: dict[str, Any], value: Any) -> list[str]:
    name = descriptor["name"]
    expected = _json_type(descriptor)
    if expected == "boolean":
        if not isinstance(value, bool):
            raise ToolError(f"{name!r} must be a boolean, got {type(value).__name__}")
        if value:
            return [_flag(descriptor)]
        negative = _negative_flag(descriptor)
        # For a plain flag, false is an absent flag; `--force false` would arm
        # the run. For a paired one, false has its own spelling and omitting it
        # would quietly keep a default the caller asked to change.
        return [negative] if negative else []
    if expected == "integer" and (
        isinstance(value, bool) or not isinstance(value, int)
    ):
        raise ToolError(f"{name!r} must be an integer, got {type(value).__name__}")
    if expected == "number" and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise ToolError(f"{name!r} must be a number, got {type(value).__name__}")
    if expected == "string" and not isinstance(value, str):
        raise ToolError(f"{name!r} must be a string, got {type(value).__name__}")
    allowed = descriptor.get("allowed_values")
    if allowed and value not in allowed:
        raise ToolError(f"{name!r} must be one of {sorted(allowed)}, got {value!r}")
    return [_flag(descriptor), str(value)]


def argv_for(metadata: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Turn a tool call into the argv the CLI would have received.

    The command line is the only interface: the server runs the real CLI, so
    the safety guard, the exit codes, and the JSON envelope all come from the
    one implementation that is already tested. Nothing here decides whether a
    run is allowed.
    """

    if not isinstance(arguments, dict):
        raise ToolError("Tool arguments must be an object")

    by_name = {
        descriptor["name"]: descriptor
        for descriptor in metadata["options"] + metadata["arguments"]
        if descriptor["name"] not in CONTROLLED_OPTIONS
    }
    synthetic_yes = _needs_synthetic_yes(metadata)

    unknown = set(arguments) - set(by_name) - ({"yes"} if synthetic_yes else set())
    if unknown:
        raise ToolError(f"Unknown argument(s): {', '.join(sorted(unknown))}")

    # `--output json` is global so it applies whether or not the command
    # declares its own; the same flag before the command name is what a human
    # would type.
    globals_: list[str] = ["--output", "json"]
    if synthetic_yes and arguments.get("yes"):
        if not isinstance(arguments["yes"], bool):
            raise ToolError("'yes' must be a boolean")
        globals_.append("--yes")

    positional: list[str] = []
    options: list[str] = []
    for name, descriptor in by_name.items():
        if name not in arguments:
            continue
        value = arguments[name]
        if value is None:
            continue
        if descriptor.get("flags"):
            options.extend(_render(descriptor, value))
        else:
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ToolError(f"{name!r} must be a scalar")
            positional.append(str(value))

    return [*globals_, *metadata["name"].split(" "), *positional, *options]
