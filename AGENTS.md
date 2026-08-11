# AGENTS.md

Working agreement for AI agents contributing to FlashCrucible. Humans are welcome to read it too;
it is short on purpose.

FlashCrucible tests microSD cards. Its commands write to block devices, and people make
buy/scrap decisions from its output. Both facts drive everything below.

## Getting started

```bash
uv sync
uv run tfqa --help
```

Before opening a pull request:

```bash
make verify
```

That is the single gate: lint, formatting, types, the tests, the same tests with every external
tool hidden, and the shipped JSON schemas. CI runs the same target, so a local pass and a green
build cannot mean different things. `make help` lists the individual targets, and `make format`
applies formatting and autofixes.

## The rules that matter here

These are not style preferences. Each one exists because breaking it produced a real bug in this
repository.

### 1. Never invent a measurement

If the device cannot supply a value, report that it is unavailable and why. Do not substitute a
plausible number, a default, or anything derived from the device path or from other device
properties.

The health readers once returned values derived from `hash(device_path)`. Python randomises string
hashing per process, so the same card reported a different serial number and wear figure on every
run — and those values were written into the run history and aggregated by `tfqa trends`.

Practical form: raise a typed error from the reader, and let the caller record `available: false`
with a reason. See `tfqa/tests/health/snapshot.py` for the shape to copy.

An engine that cannot do real work must say so rather than estimate: `performance` and
`surface-scan` let `ToolNotFoundError` propagate when their tool is missing, and `endurance`
raises `NotImplementedEngineError` because it does no device I/O at all. Callers decide what to do
— a `pipeline` records the stage as `skipped` rather than failing the run.

### 2. Never swallow an error

`except Exception: pass` and `except OSError: pass` have both hidden real failures here. A
malformed profile silently vanished from `tfqa profiles`; an `fsync` failure let a test report
success for data the device never committed.

Broad `except` is acceptable in exactly one shape: a per-item loop where one bad item must not
abort the batch **and** the failure is recorded on that item. Anything else, let it propagate as a
typed error.

### 3. Anything that writes **raw blocks** must clear the safety guard

Call `_assert_device_safe()` before writing to the block device. Overriding requires **both**
`--force` and `--yes`; `--force` alone is treated as unconfirmed so a stray flag in a script
cannot arm a destructive run.

The guard refuses a mounted device, so it applies to raw writes only. Work that goes *through* a
mounted filesystem is exempt by design — `workload-smallfiles` creates files on the mounted card,
so guarding it would make the command impossible to run. It passes `check_safety=False` for the
same reason.

Read-only paths stay usable on a mounted card. When in doubt, check what the command actually
does rather than what its name suggests — a pipeline's `surface-scan` stage runs read-only even
though the standalone command can write, which is why
`tfqa.orchestration.pipeline.DESTRUCTIVE_STAGES` excludes it and records why.

### 4. Anything that writes must support `--dry-run`

Both the global `tfqa --dry-run <command>` and the command's own flag. Use `_resolve_dry_run()`
and `_emit_dry_run()`; do not open-code a plan payload.

The check goes **after** argument validation, so a dry run still rejects bad arguments, and
**before** the safety guard, so it can preview a refusal rather than raise one.

### 5. Validation rules get one home

If the engine rejects a value, the CLI must reject it identically before emitting a dry-run plan.
Put the rule in a `validate_*()` function in the engine module and call it from both. See
`tfqa.tests.capacity.full.validate_options` and `tfqa.tests.endurance.simple.validate_config`.

### 6. Status strings must survive the pipeline

Engines report `"ok"` / `"fail"`; the pipeline vocabulary is `"ok"` / `"warning"` / `"failed"` /
`"skipped"` / `"error"`. `normalize_status()` maps between them, and an unrecognised value becomes
`"error"`, never `"ok"`. Do not add a status without checking it round-trips — an unmapped `"fail"`
once caused detected counterfeits to be recorded as passing pipeline stages.

### 7. Tests must not depend on the host

The suite must pass on a machine with none of the external tools installed. Patch the tool lookup,
not just the function that uses it — three CLI tests once passed only because `f3probe` happened
to be installed locally, and broke in CI.

Verify with `make test-hermetic`, which is part of `make verify` and runs in CI.

### 8. A regression test must fail against the old code

When you fix a bug, revert the fix, confirm the new test fails, then restore it. A test that
passes both before and after proves nothing. This has repeatedly caught fixes that did not do what
they claimed.

## These rules are enforced, not just written down

`tests/test_command_surface_invariants.py` reads the command surface out of the
Typer tree and checks rules 1, 3, 4 and 6 mechanically. A new command taking
`--device` fails the build unless it calls the safety guard and supports
`--dry-run`, or is listed with a recorded reason. `describe` must also agree
with the code about which commands are destructive.

The command list is derived, not maintained, so adding a command adds it to the
checks. Only the exemptions are written down, and each carries its reason.

## Repository layout

| Path | Contents |
| --- | --- |
| `tfqa/cli/main.py` | Every command, argument parsing, output rendering |
| `tfqa/core/` | Devices, config, logging, capabilities, safety, paths, models |
| `tfqa/tests/` | Test engines, one package per category |
| `tfqa/ext/` | Thin wrappers around external binaries |
| `tfqa/orchestration/` | Pipeline sequencing, endurance profiles, workflow combos |
| `tfqa/reporting/` | Run history index, summaries, trend aggregation |
| `tfqa/data/` | Profiles, workflow combos, JSON schemas — shipped inside the package |

`tfqa/data/` lives inside the package deliberately: data outside it is not included in the wheel,
which once left the installed CLI unable to find any profile.

## Output contract

Every command returns the same envelope, defined by
`tfqa/data/schemas/json/cli_response.schema.json`. Exit codes are stable and defined in
`tfqa/core/error_codes.py`: `0` success, `1` test failed, `2` invalid arguments or configuration,
`3` environment problem.

Changing an envelope field, an exit code, or a `data` key that automation reads is a breaking
change. Say so in the pull request.

## Working with hardware

Do not run destructive commands against a real device to check your work. Use `--dry-run`, or a
file standing in for a block device as the engine tests do. If you genuinely need a device, ask
the human first and confirm which one — `tfqa detect` lists what is attached, and the wrong answer
destroys somebody's data.

## Pull requests

- Explain what was wrong, not only what changed.
- Say how you verified it, including what you checked that could have proved you wrong.
- Flag anything you are unsure about; a reviewer who knows where to look is worth more than a
  confident summary.
