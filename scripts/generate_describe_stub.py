#!/usr/bin/env python3
"""Emit a machine-readable describe JSON for commands.

Behavior:
- If `tfqa.cli` package exists and exposes a Typer/Click app, introspect it and emit a
  machine-readable description of available commands (name, help, top-level params).
- Otherwise fall back to a small built-in stub for `quick-test`.

This helper is intentionally tolerant: it avoids import errors and emits a simple
JSON envelope consumable by agents.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from typing import Any, Dict, Iterable, Optional


STUB_COMMANDS: Dict[str, Any] = {
    "quick-test": {
        "name": "quick-test",
        "summary": "Fast capacity/authenticity check using F3 or native sampling.",
        "destructive": False,
        "requires_root": False,
        "arguments": [
            {"name": "device", "type": "string", "required": True, "position": 1}
        ],
        "options": [
            {
                "name": "--output",
                "type": "string",
                "default": "human",
                "allowed_values": ["human", "json"],
            },
            {"name": "--non-interactive", "type": "bool", "default": False},
        ],
    }
}


def _describe_click_param(p: Any) -> Dict[str, Any]:
    """Describe a Click/Typer parameter (Option or Argument).

    Return a small dictionary with name, required, type, and flags (for options).
    """
    name = getattr(p, "name", None)
    required = bool(getattr(p, "required", False))
    kind = type(p).__name__
    ptype = getattr(p, "param_type_name", kind)
    out: Dict[str, Any] = {"name": name, "required": required, "type": ptype}
    opts = getattr(p, "opts", None)
    if opts:
        out["flags"] = list(opts)
    return out


def _describe_click_cmd(cmd: Any) -> Dict[str, Any]:
    """Convert a Click Command object into a simple dict description.

    We only extract parameter names, whether required, and whether it's an option or arg.
    """
    name = getattr(cmd, "name", "")
    summary = getattr(cmd, "help", None)
    callback = getattr(cmd, "callback", None)
    if not summary and callback and getattr(callback, "__doc__", None):
        summary = (callback.__doc__ or "").strip()

    out: Dict[str, Any] = {
        "name": name,
        "summary": summary,
        "destructive": False,
        "requires_root": False,
        "arguments": [],
        "options": [],
    }

    params = getattr(cmd, "params", []) or []
    for p in params:
        pinfo = _describe_click_param(p)
        # heuristics: presence of opts -> option, else argument
        if pinfo.get("flags"):
            out["options"].append(pinfo)
        else:
            out["arguments"].append(pinfo)

    return out


def _load_cli_module(module_path: str = "tfqa.cli.main") -> Optional[Any]:
    try:
        return importlib.import_module(module_path)
    except Exception:
        return None


def _find_app_candidates(mod: Any) -> Iterable[Any]:
    """Yield candidate Typer/Click app objects from the module.

    This keeps detection logic small and testable.
    """
    for name in ("app", "cli", "typer"):
        if hasattr(mod, name):
            yield getattr(mod, name)


def introspect_typer() -> Dict[str, Any]:
    """Attempt to import `tfqa.cli` and introspect a Typer/Click app.

    Returns a dict of command descriptions. On any failure, returns an empty dict.
    """
    mod = _load_cli_module()
    if mod is None:
        return {}
    for app in _find_app_candidates(mod):
        # Try each strategy in a small helper to keep cognitive complexity low.
        try:
            desc = _try_registered_commands(app)
            if desc:
                return desc

            desc = _try_commands_dict(app)
            if desc:
                return desc

            desc = _try_generic_object(app)
            if desc:
                return desc
        except Exception:
            # skip failing candidate
            continue

    return {}


def _try_registered_commands(app: Any) -> Dict[str, Any]:
    """Return descriptions if app has `registered_commands` (Typer)."""
    if not hasattr(app, "registered_commands"):
        return {}
    try:
        return {
            c.name: _describe_click_cmd(c) for c in getattr(app, "registered_commands")
        }
    except Exception:
        return {}


def _try_commands_dict(app: Any) -> Dict[str, Any]:
    """Return descriptions if app has a Click-style `commands` dict."""
    if not hasattr(app, "commands"):
        return {}
    try:
        return {
            name: _describe_click_cmd(c) for name, c in getattr(app, "commands").items()
        }
    except Exception:
        return {}


def _try_generic_object(app: Any) -> Dict[str, Any]:
    """Describe callables on a generic object as commands."""
    if inspect.ismodule(app) or not hasattr(app, "__dict__"):
        return {}
    try:
        result: Dict[str, Any] = {}
        for attr_name, attr in vars(app).items():
            # narrow types for static checkers
            if not isinstance(attr_name, str):
                continue
            if getattr(attr, "__call__", None) is not None and not attr_name.startswith(
                "_"
            ):
                doc = getattr(attr, "__doc__", "") or ""
                summary = str(doc).strip()
                result[attr_name] = {
                    "name": attr_name,
                    "summary": summary,
                    "destructive": False,
                    "requires_root": False,
                    "arguments": [],
                    "options": [],
                }
        return result
    except Exception:
        return {}


def main(cmd: Optional[str] = None) -> None:
    commands = introspect_typer() or STUB_COMMANDS
    out: Any
    if cmd:
        out = commands.get(cmd, {})
    else:
        out = dict(commands)

    envelope: Dict[str, Any] = {"status": "ok", "command": "describe", "data": out}
    print(json.dumps(envelope, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
