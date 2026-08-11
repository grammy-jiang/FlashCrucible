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

Before opening a pull request, all of these must pass:

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # formatting (CI fails on any diff)
uv run mypy tfqa/ tests/         # types
uv run pytest -q                 # tests
uv run tfqa validate-schemas     # the shipped JSON schemas parse
```

`ruff format` is enforced by its own CI job, so run `uv run ruff format .` before pushing.

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

**This rule is not yet true everywhere.** Both performance engines still synthesise throughput
when `fio` is missing (`tfqa/tests/performance/basic.py:80`, `random.py:125`) and return
`status: "ok"`. They mark it with `details["mode"] = "simulated"`, but `trends` aggregates
`metrics`, not `details`, so the invented figures reach trend analysis unlabelled. Do not copy
that pattern, and do not treat its presence as precedent — it is a known defect awaiting a fix.

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

Verify with:

```bash
uv run python - <<'PY'
import unittest.mock as m, shutil, pytest
HIDE = {"f3probe","f3write","f3read","badblocks","fio","mmc","sdmon","smartctl",
        "dd","cmp","fsck","e2fsck","fsck.vfat","fsck.exfat","blkdiscard","hdparm","stress-ng"}
real = shutil.which
with m.patch("shutil.which", lambda c, *a, **k: None if str(c).split("/")[-1] in HIDE else real(c, *a, **k)):
    raise SystemExit(pytest.main(["-q", "tests/"]))
PY
```

### 8. A regression test must fail against the old code

When you fix a bug, revert the fix, confirm the new test fails, then restore it. A test that
passes both before and after proves nothing. This has repeatedly caught fixes that did not do what
they claimed.

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
