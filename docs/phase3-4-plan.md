# Phase 3/4 Work Plan

This note captures the remaining roadmap work described in `docs/features-roadmap-v0.md` after the initial automation and Phase 2 surface/performance engines were sketched out. It focuses on the **endurance/orchestration layers** and the **automation/reporting hooks** mentioned in steps 3 and 4, and it keeps an eye toward deliverables and tests.

## 1. Endurance, Burn-in & Workload (Phase 3)

- **E1 / Simple burn-in loop**

  - Build a new `tfqa.tests.endurance.simple` module that writes/reads configurable patterns (or stubs for the first version) and emits metrics such as total bytes written, errors, and throughput.
  - Provide a `RunContext`/`TestResult` shim that integrates with existing logging (JSONL event emission) so each cycle becomes a structured run.
  - Tests: unit tests that mock the underlying I/O helpers and verify metrics are aggregated, plus a CLI test that exercises the `endurance` command in `tfqa.cli.tests` (with `CliRunner`).

- **E2 / Configurable endurance profiles**

  - Add profile parsing (TOML/YAML) under `data/profiles/` (already exist) and expose a `tfqa.orchestration.profile` helper that resolves duration, size, and pass counts.
  - Validate profiles in `tfqa.cli.config` and expose `--profile <name>` to endurance runs.
  - Tests: profile validation unit tests and CLI tests verifying the selected profile parameters propagate to the engine.

- **E3 / Trend analysis hooks (in progress)**
  - Extend `tfqa.reporting.summary` to accept multiple JSONL files and emit time-series (e.g., throughput vs time) for future graphs. (Implemented: the helper now resolves multiple logs, builds `metrics_by_stage`, and returns a `time_series`/`events`-rich summary.)
  - Tests around `tfqa.reporting.summary` should assert the aggregated metrics respect the input events and exposures. (New tests cover this broader aggregation identity.)

## 2. Orchestration & Automation (Phase 3/4)

- **U3 / Profile-based orchestration (delivered)**

  - Create a `tfqa.orchestration.pipeline` module that can sequence: detect → quick test → full test → surface/performance/endurance → health → report. (Implemented with default stage order and stage payload logging.)
  - Accepts a profile (TOML) or command-line list of stages, runs each stage in order, and emits combined `TestResult` objects plus JSONL events. (The CLI pipeline command now records each stage and persists history/log metadata.)
  - Add an explicit `--stages` option for the CLI pipeline so automation can run targeted subsets (e.g., `--stages detect,quick-test,health`). The option validates stage names, returns the negotiated stage plan inside the JSON response, and records the requested stage sequence in history entries.
  - Provide structured combos via `tfqa pipeline --combo` and `tfqa combos`, exposing the curated stage order, image defaults, and preferred profiles so automation doesn’t need to spell out the plan. (Implemented: `tfqa.orchestration.workflows` plus CLI wiring.)
  - Tests: pipeline unit tests that mock each stage, ensuring the proper sequence, logging, and error propagation. (CLI tests and summary hooks cover the pipeline output shape.)

- **R3 / History & catalog (delivered)**

  - Add a lightweight history index (JSON or SQLite) under `~/.config/tfqa/history.jsonl` that records run metadata (device, command, status, run_id). (Implemented: `tfqa.reporting.history.record_run` + `read_history` are used by the pipeline and CLI.)
  - Provide `tfqa.cli.history` command to query runs; tie into CLI tests verifying the command returns expected entries after simulated runs. (CLI tests now mock history data, and the command supports filtering + JSON output.)
  - Expose profile discovery through `tfqa profiles`, listing every `data/profiles/*.toml` entry with `duration_seconds`, `pass_count`, `force`, `write_pattern`, and `path` metadata so automation can choose endurance presets directly from the CLI.
  - Document how automation can read the `metadata.stage_plan`, `requested_stages`, and `combo` info recorded in history entries, enabling `trends` or `report` runs to replay or audit specific sequences and health snapshots.

- **Automation-friendly CLI & logging**

  - Ensure every command (detect, quick-test, full test, health, performance, report, endurance, surface) logs JSONL events via `tfqa.core.logging.emit_event`, including run metadata, phase name, metrics.
  - Expand CLI tests to cover JSON output for automation (already done for describe/capabilities). Add tests verifying new commands respect `--output json`, `--non-interactive`, `--dry-run`, and emit the correct `error_code` states.
  - CLI regression tests now include `pipeline`, `history`, and `report` to validate orchestration outputs and history recording.
  - Add CLI tests for `tfqa combos` and `tfqa profiles` that validate the JSON payloads, image/profile metadata, and filtering logic so automation can discover structured combos and endurance presets reliably.
  - Document stable exit codes and automation hooks in README (already partially done). Add regression tests that parse JSON output to verify the shape matches `tfqa/core/models.CLIResponse`.
  - Captured the new CLI metadata: `quick-test` now converts numeric metrics to floats before emitting events via `logging.emit_event`, `METRICS_LABEL` centralizes metric summaries, and `image-flash` history entries include a typed `metadata` block (`image_path`, `block_size`, `conv_flags`, `verify`, timeouts) so automation can inspect what was run.
  - The `report` command now pulls the latest history entry, replays the associated JSONL log, and exposes the enriched summary (including `duration_seconds`, `stage_summaries`, and `metrics_series_by_stage`) in both human and JSON modes, as validated by `tests/test_reporting_summary.py` and `tests/test_cli_report_config.py`.

- **Automation report hook (delivered)**
  - `tfqa automation-report` now composes `history_entry`, `summary`, and `trends` into `CLIResponse.data.report`, matching the payload described in the README, and exposes `_stage_metric_lines` so automation sees stage counts, status counts, duration averages, and per-metric averages in its logs.
  - Use `--push` plus `automation_report.endpoints` (URL/method/headers/timeout plus `max_attempts`, `backoff_seconds`, `backoff_factor`, `fail_on_error`) to push the JSON payload to remote services; `_push_automation_report` now retries transient failures and records each endpoint’s `attempts`, `status`, `body`, `success`, and `last_error`. Setting `fail_on_error` raises `error_code=REMOTE_PUSH_FAILED` when telemetry endpoints never succeed so automation can react deterministically.
  - Run `tfqa describe-schemas --schema automation_report.schema.json --output json` to discover the exact payload shape rendered under `CLIResponse.data.report`, giving automation a stable blueprint for parsing the cron output.

Remaining follow-up items:

- Ensure documentation (README/docs/phase3-4-plan) highlights the new automation hooks, the automation-report payload, the profile discovery command, and the rich history/trends metadata now accessible via JSON responses. (CURRENT)
- Highlight `tfqa describe-schemas --schema <name>` so automation can inspect the schema files under `data/schemas/json` (e.g., `automation_report.schema.json`, `summary.schema.json`, `trends.schema.json`, and `cli_response.schema.json`) before building downstream validators.

## 3. Automation Reporting & Verification (Phase 4+)

- **Run summary & machine-readable reports (delivered)**

  - Expand `tfqa.reporting.summary` to emit both human-readable and JSON summaries (matching the schemas under `data/schemas/json`). (Now returns `metrics_by_stage`, `time_series`, and raw `events` for richer reporting.)
  - Document the `summary` and `trends` JSON schema assets under `data/schemas/json` so downstream automation can validate the outputs of `tfqa report` and `tfqa trends`.
  - Expose those schema assets via `tfqa describe-schemas --output json` so automation can fetch titles, versions, and full schema definitions before validating `report`, `summary`, or `trends` payloads.
  - Add CLI command `tfqa report <run-id>` to fetch from history + JSONL logs and print structured output. (`tfqa report` now resolves the logged run, returns the summary, and surfaces history metadata in both JSON and human modes.)
  - Tests: sample JSONL fixtures (`tests/fixtures/sample-run.jsonl`) already exist—extend tests to ensure the report command returns expected metrics and respects `--output json`. (Added `tests/test_reporting_summary.py` and updated CLI report tests accordingly.)

- **Automation hooks for config & describe (already available)**
  - Continue to keep the `describe` registry up-to-date (e.g., when new commands like `endurance` are added) and add tests verifying each command is documented.
  - Add a CLI test that calls `tfqa describe --output json` for the new pipeline/orchestration commands once implemented.
  - Add a `tfqa trends` command that summarizes numeric stage metrics across history entries, supporting `--stage` filtering, `--limit`, and JSON-ready averages so automation can detect regressions at a glance.

## 4. Dependencies & Next Steps

- **External tool dependencies**: plan for wrappers or fallbacks for `fio`, `badblocks`, `sdmon`, `smartctl`, and `dd` operations; each needs capability detection via `tfqa.core.capabilities` and configurable timeouts.
- **Testing strategy**: prefer mocking subprocesses rather than calling actual system tools; use fixtures for devices/profiles.
- **Next Execution**: re-run the relevant CLI test suites (`tests/test_cli_*`) plus targeted unit tests (surface, performance, endurance, reporting, history) and ensure `uv run ruff`/`uv run mypy` still pass. (Reporting/history tests already run with `uv run pytest tests/test_reporting_summary.py tests/test_cli_report_config.py tests/test_cli_history.py tests/test_cli_pipeline.py`.)
