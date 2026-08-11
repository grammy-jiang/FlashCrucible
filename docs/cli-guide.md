# CLI Guide

Detailed notes on individual `tfqa` commands, their payloads, and the automation hooks built on
top of them. For a general introduction see the [README](../README.md); for the design rationale
behind the UX rules see [ux-v0.md](ux-v0.md).

> Several sections below reference `data/profiles/` and `data/workflows/`. Because of a path bug
> (README, *Known limitations* #1), the defaults resolve to a non-existent `tfqa/data/` directory.
> Pass `--profiles-dir data/profiles` or set `TFQA_WORKFLOWS_DIR=data/workflows` until it is fixed.

## Contents

- [UX requirements](#ux-requirements)
- [Development and QA commands](#development-and-qa-commands)
- [CLI tests and mocking](#cli-tests-and-mocking)
- [Discovery hooks](#discovery-hooks)
- [Pipelines and combos](#pipelines-and-combos)
- [Image flashing](#image-flashing)
- [Small-file workload](#small-file-workload)
- [Surface scan and performance instrumentation](#surface-scan-and-performance-instrumentation)
- [Profiles and endurance metadata](#profiles-and-endurance-metadata)
- [History, trends, and reports](#history-trends-and-reports)
- [Automation report pushes](#automation-report-pushes)
- [Structured metric conventions](#structured-metric-conventions)

## UX requirements

FlashCrucible is an interactive CLI and treats UX as a requirement, not a polish item.

- **Command model and discoverability.** Verb + resource subcommands (`detect`, `quick-test`,
  `endurance`, `report`). Positional arguments carry the main resource; flags carry behaviour.
  `--help` stays concise but useful, with a few examples, defaults, and capability notes.
- **Safety first.** Never default to a device; force explicit selection. Confirm destructive
  actions with loud prompts unless `--yes` / `--non-interactive` is set. Offer read-only and
  dry-run modes wherever possible.
- **Dry runs.** Every command that writes accepts `--dry-run`, either globally
  (`tfqa --dry-run <command>`) or as the command's own flag (`tfqa <command> --dry-run`). Both
  print `CLIResponse.data.plan` and execute nothing. The plan carries a `safety` block
  (`would_run`, `error_code`, `reason`, `details`) reporting whether the real run would clear the
  destructive-operation guard, so automation can check a device without attempting a write.
  Commands that never clear the unmounted requirement — `workload-smallfiles`,
  `surface-scan --mode readonly`, read-only `pipeline` plans — omit the block rather than predict
  an inapplicable refusal.
- **Destructive guardrails.** Every command that writes raw blocks calls
  `tfqa.core.safety.assert_safe_for_destructive`, which enforces that the device is neither a
  system disk nor currently mounted. On failure the CLI raises `DeviceUnsafeError` (error code
  `DEVICE_UNSAFE`, exit code 3) with details such as `is_system_disk` and `mountpoints`, and
  instructs automation to retry with `--force --yes`. Both flags are required — `--force` alone
  is treated as unconfirmed. Read-only paths (`surface-scan --mode readonly`, a non-writing
  `pipeline` plan, `detect`, `health`, reporting) stay usable on a mounted card, and
  `workload-smallfiles` is exempt because it writes through a mounted filesystem. Covered by
  `tests/test_core_safety.py`, `tests/test_cli_safety_guard.py`, and
  `tests/test_cli_full_capacity.py`.
- **Progress and responsiveness.** Heartbeats and progress (bars for TTY, periodic text
  otherwise), approximate ETAs, clear phase labels with step position (`Step 2/5: verify`).
- **Interrupts and robustness.** Handle Ctrl+C at safe points, mark runs aborted, point at partial
  logs. Run preflight checks for tool availability, permissions, and device access early.
- **Logging and reporting.** Separate human and machine output. Console defaults to INFO plus
  progress; `--verbose` adds DEBUG, `--quiet` trims to warnings/errors plus a one-line summary.
  Persist timestamped logs, prefer JSONL for structured events (`ts`, `run_id`, `device`, `phase`,
  `metrics`), keep DEBUG/TRACE in files.
- **Run metadata.** At start emit version, host, device info, command line, and effective config.
  At end give a concise PASS/FAIL/ABORTED summary with key metrics versus thresholds, plus paths
  to logs and reports.
- **Configuration transparency.** CLI args > env vars > config files > defaults. Validate early,
  print each effective value with its source, and provide a `config show` view.
- **Automation and exit codes.** Document stable exit codes (0 success, 1 test failed, 2
  config/args error, 3 environment/system error). Keep long option names stable; reserve short
  flags for common toggles (`-v`, `-q`, `-y`, `-h`).
- **Noise discipline and accessibility.** Keep output compact, aggregate by time window or phase,
  respect `NO_COLOR` / `--no-color`, make errors actionable with remediation hints.
- **Anti-patterns to avoid.** Hidden magic defaults, silent fallbacks, wizards that block
  automation, and mixing human logs with machine JSON in one stream without separation.

## Development and QA commands

```bash
# Bootstrap the virtual environment and dependencies
uv sync

# Lint a subset
uv run ruff check tfqa/cli/main.py tfqa/tests/health

# Run the CLI-focused unit tests
uv run pytest tests/test_cli_detect.py tests/test_cli_quick_test.py

# Static typing guardrails
uv run mypy tfqa/cli tfqa/core

# Invoke the CLI
uv run tfqa detect --output json
```

The same pattern works for other workflows, e.g. `uv run tfqa quick-test --device /dev/fake
--dry-run`, `uv run pytest tests/test_cli_*`, `uv run mypy tfqa/tests`.

For automation-friendly inspection:

```bash
uv run tfqa capabilities --output json
uv run tfqa describe quick-test --output json
uv run tfqa config show --output json
uv run tfqa config validate --output json
uv run tfqa endurance --device /dev/fake --output json
```

## CLI tests and mocking

The suites under `tests/test_cli_*.py` subclass `unittest.TestCase` and use Typer's `CliRunner` to
invoke each command. Rather than `pytest.MonkeyPatch`, they call `unittest.mock.patch` inside a
`with` block to scope device and function stubs (`tfqa.core.devices.get_device`,
`tfqa.tests.capacity.quick`, and so on). No test touches real hardware.

```bash
uv run pytest tests/test_cli_*          # fast sanity check across the CLI surface
```

## Discovery hooks

These commands let humans and AI agents stay synchronised with the CLI without hardcoding it.

- **`capabilities --output json`** reports `Capabilities` metadata from `tfqa.core.capabilities`:
  external tool availability, feature modes (native/wrapper/disabled), and platform info.
- **`describe <command> --output json`** returns a stable schema (`CLIResponse.data.describe`)
  documenting arguments, options, defaults, destructive flags, and required privileges. The human
  output stays a concise overview.
- **`describe-schemas --output json`** lists every JSON schema under `data/schemas/json` with its
  title, schema version, and description. Narrow with `--schema <name>` (e.g. `--schema
  cli_response` or `cli_response.schema.json`).
- **`validate-schemas --output json`** parses each schema with `jsonschema.Draft7Validator.check_schema`.
  The response includes `data.files` (status, errors, hints, metadata issues) and `data.failed`, so
  automation can act on exactly which files failed and why.
- **`lint-schemas --output json`** ensures each schema declares both `title` and `schema_version`.
  `data.issues` records missing fields plus hints so automation can self-heal metadata.
- **`config show` / `config validate`** surface the merged configuration tree with source hints.

Override the schema directory with `TFQA_SCHEMAS_DIR` or `schemas_dir` in the config:

```bash
TFQA_SCHEMAS_DIR=/tmp/custom-schemas uv run tfqa validate-schemas --output json
```

## Pipelines and combos

`tfqa pipeline` accepts `--stages <stage,...>` so automation or humans can run targeted segments:

```bash
uv run tfqa pipeline --device /dev/sdX --stages detect,quick-test,health
```

The response exposes both the negotiated stage sequence (`data.stage_plan`) and the requested plan
(`data.requested_stage_plan`), so automation can distinguish what ran from what was asked for. The
history entry mirrors this as `metadata.stage_plan` and `metadata.requested_stages`, which lets
downstream reporting replay the same sequence or audit overrides.

Curated combos live in `data/workflows/structured-combos.toml` (`camera-logger`,
`router-telemetry`, `full-capacity`). Pass `--combo <name>` instead of listing stages manually and
the CLI runs the curated plan, picks the combo's recommended profile, and applies any image
defaults it declares. The JSON response and history metadata include a `combo` record (name,
description, profile, stage list, optional image defaults) plus the standard `image_options`.

```bash
uv run tfqa combos                                    # list available combos
uv run tfqa pipeline --device /dev/sdX --combo camera-logger
```

`pipeline` also supports an `image-flash` stage. Pass `--image-path` along with
`--image-block-size`, `--image-conv-flags`, `--image-write-timeout`, and `--image-verify-timeout`
when the plan includes `image-flash`, and the orchestration flow writes a golden image via `dd`,
optionally verifying with `cmp`.

## Image flashing

`tfqa image-flash` writes a canonical image file to a removable device and, by default, performs a
byte-by-byte verification afterwards.

```bash
uv run tfqa image-flash --device /dev/sdX --image-path ./golden.img \
  --block-size 4M --conv-flags fsync,noerror --write-timeout 600 --verify-timeout 600
```

`--conv-flags` takes comma-separated `dd` conv flags. The command emits JSONL events, writes a
history entry, and populates the JSON envelope with the raw `run_image_flash` metrics plus the
same `image_options` metadata the pipeline stage uses, making standalone flashes directly
comparable with pipeline runs.

## Small-file workload

`tfqa workload-smallfiles` iteratively creates, reads, and optionally deletes tiny files so the
device sees metadata, allocation, and removal activity.

- `--file-count` / `--file-size` tune density.
- `--directory` pins a working set.
- `--no-delete` keeps artifacts for inspection.
- `--no-read-after-write` limits the run to create/delete stress.
- `--dry-run` previews the plan without touching the device, returning a `plan` dictionary with
  `device_path`, `file_count`, `file_size_bytes`, `working_directory`, `delete_after`, and
  `read_after_write`.

The command emits JSONL events, records each run via `tfqa.reporting.history.record_run`, and
prints human-friendly summaries plus detailed metrics under `--output json`.

## Surface scan and performance instrumentation

`surface-scan` tracks per-pass coverage, latency, and `badblocks` output, and keeps a per-run
health snapshot (MMC + `sdmon`) inside the response.

`performance` prefers `fio` (sequential or random) to report throughput, latency, and IOPS, and
falls back to a synthetic benchmark when `fio` is missing.

Every stage in `tfqa pipeline` records the latest health snapshot (`life_used_percent`,
`power_on_count`, and so on) in its JSONL event and CLI response, so automation can correlate
throughput and capacity results with device health trends. Note that health values are currently
synthetic — see README *Known limitations* #5.

## Profiles and endurance metadata

`tfqa profiles` inspects every TOML preset under `data/profiles/`, printing `name`, `description`
(defaulting to "No description"), `duration_seconds`, `pass_count`, `force`, `write_pattern`, and
the source `path`. The JSON data follows `tfqa.orchestration.profile.EnduranceProfile`, so numeric
types and booleans are stable.

```bash
uv run tfqa --profiles-dir data/profiles profiles
uv run tfqa --profiles-dir data/profiles profiles --name camera-logger --output json
uv run tfqa --profiles-dir data/profiles endurance --device /dev/sdX --profile camera-logger
```

Filtering by `--name` lets automation verify a profile before invoking a destructive endurance
loop. Use `tfqa describe-schemas --schema cli_response.schema.json --output json` to see the
envelope that wraps the `profiles` response.

## History, trends, and reports

`tfqa history` queries the recorded run index. `tfqa report` summarises metrics from a single
recorded pipeline run.

`tfqa trends` aggregates numeric metrics from the history index so teams can spot drift in
throughput, error rates, bytes written, and similar:

```bash
uv run tfqa trends --stage quick-test --output json
uv run tfqa trends --stage pipeline.performance --limit 50
```

- `--stage <stage-name>` narrows the analysis (`quick-test`, `endurance`, `pipeline.performance`).
- `--limit` controls how many past runs are scanned.
- `--output json` returns structured averages (`stage_metrics`, `entries_processed`,
  `stage_filter`, `run_ids`).

The structured `stage_metrics` payload mirrors the `_stage_metric_lines` helper: each stage record
includes a `count`, optional `occurrences` (for suffix grouping), `status_counts`, `duration`
aggregates (`count`, `average`, `last`), and per-metric averages such as latency and throughput.
Automation can read `CLIResponse.data.trends.stage_filter` to learn which suffixes matched, and use
the per-stage averages to detect regressions without replaying logs.

Confirm the exact JSON shape with:

```bash
uv run tfqa describe-schemas --schema trends.schema.json --output json
```

## Automation report pushes

`tfqa automation-report` bundles a single `history_entry`, its `summary`, and aggregated `trends`
into `CLIResponse.data.report`, matching `data/schemas/json/summary.schema.json` and
`trends.schema.json`. Validate the whole payload with `tfqa describe-schemas --schema
automation_report.schema.json --output json`, or validate individual blocks against the
`summary` and `trends` schemas.

Pass `--push` to deliver the payload to each endpoint configured under
`[automation_report].endpoints`:

```toml
[automation_report]
endpoints = [
  { name = "ci-dashboard",
    url = "https://ci.example.org/runs",
    method = "POST",
    headers = { "X-API-Key" = "secret" },
    timeout = 20,
    max_attempts = 5,
    backoff_seconds = 2,
    backoff_factor = 1.5,
    fail_on_error = true
  }
]
```

`_push_automation_report` retries transient failures with exponential backoff and records each
endpoint's `attempts`, `status`, `body`, `success`, and `last_error` inside
`CLIResponse.data.remote_push`. Setting `fail_on_error = true` makes FlashCrucible abort with
`error_code=REMOTE_PUSH_FAILED` if an endpoint never succeeds, giving automation a deterministic
signal when a downstream system is down. All requests carry `Content-Type: application/json`.

## Structured metric conventions

`quick-test`, `image-flash`, and `workload-smallfiles` convert numeric metric values to `float`,
emit them via `tfqa.core.logging.emit_event` under the canonical `metrics` key, and print them
prefixed with the shared `METRICS_LABEL` string. Each CLI response includes a `data` payload that
preserves this structure, so automation parsing `CLIResponse.data` always sees `metrics` as floats
and `details` as dicts.

History entries and pipeline records keep a typed `metadata` block — for example `image_options`
with the image path, block size, conv flags, verify toggle, and timeouts — alongside the negotiated
and requested stage plans. The `history`, `pipeline`, and `report` commands expose that metadata
through their JSON responses, so downstream tooling can audit what ran, replay the same plan, or
visualise which devices saw which metrics without re-parsing log files.

The profile presets under `data/profiles/` (`camera-logger.toml`, `router-telemetry.toml`) show
how workloads can be packaged into reusable configurations that pair endurance passes with
small-file stress plans for different product classes.
