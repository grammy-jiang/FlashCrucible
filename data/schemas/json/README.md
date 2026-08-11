# JSON Schemas for FlashCrucible

This folder contains JSON Schema files for primary runtime models used by AI agents and automation.

Files:

Versioning & compatibility:

Automation clients can run `tfqa describe-schemas --output json` to list these files along with their titles, schema versions, and descriptions. Use `--schema <name>` to filter to a single schema and set `TFQA_SCHEMAS_DIR` (or `schemas_dir` in the config) when working from a custom schema directory.
Run `tfqa validate-schemas --output json` (optionally with `--schema <name>`) to ensure each schema is a valid draft-07 document. The command uses `jsonschema.Draft7Validator.check_schema`, returns `data.files` with status/errors/hints, and adds `data.failed` plus an `errors` list so automation can decide which schema to fix.
Use `tfqa lint-schemas --output json` to verify every schema declares both `title` and `schema_version`; `data.issues` lists the missing fields and a hint for remediation.

Override the schema directory in automation with the `TFQA_SCHEMAS_DIR` environment variable when you maintain a curated schema bundle, e.g.: `TFQA_SCHEMAS_DIR=/tmp/custom-schemas uv run tfqa validate-schemas --output json`.
