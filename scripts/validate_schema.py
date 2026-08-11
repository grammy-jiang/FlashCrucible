#!/usr/bin/env python3
"""Validate a JSON instance against a schema.

Usage:
  python scripts/validate_schema.py <schema.json> <instance.json>

This helper will use `jsonschema` if available. If not present, it falls back to a
lightweight required-field check (only validates presence of top-level required keys).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def simple_validate(
    schema: dict[str, Any], instance: dict[str, Any]
) -> tuple[bool, str]:
    """Perform a minimal validation: ensure top-level required keys are present.

    This is a fallback when the `jsonschema` package is not installed.
    """
    required = schema.get("required") or []
    missing = [k for k in required if k not in instance]
    if missing:
        return False, f"Missing required fields: {missing}"
    return True, "OK (simple validation)"


def main(schema_path: Path, instance_path: Path) -> int:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    instance = json.loads(instance_path.read_text(encoding="utf-8"))

    try:
        import jsonschema

        jsonschema.validate(instance=instance, schema=schema)
        print("VALID: instance conforms to schema (jsonschema)")
        return 0
    except ImportError:
        ok, msg = simple_validate(schema, instance)
        if ok:
            print(f"VALID: {msg}")
            return 0
        else:
            print(f"INVALID: {msg}")
            return 2
    except Exception as e:
        print(f"INVALID: {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: validate_schema.py <schema.json> <instance.json>")
        raise SystemExit(2)
    schema_p = Path(sys.argv[1])
    inst_p = Path(sys.argv[2])
    raise SystemExit(main(schema_p, inst_p))
