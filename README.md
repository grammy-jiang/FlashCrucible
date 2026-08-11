# FlashCrucible

[![CI](https://github.com/grammy-jiang/FlashCrucible/actions/workflows/ci.yml/badge.svg)](https://github.com/grammy-jiang/FlashCrucible/actions/workflows/ci.yml)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/grammy-jiang/FlashCrucible/blob/master/LICENSE)
[![Linting: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![Platform: Linux](https://img.shields.io/badge/platform-linux-lightgrey.svg)](#platform-support)

**A Linux command-line tool for testing microSD / TF cards.** It tells you whether a card is
counterfeit, whether its surface is failing, how fast it really is, and how much life it has left.

The CLI is called `tfqa`.

```bash
uv sync
uv run tfqa detect                    # what cards are attached?
uv run tfqa capabilities              # which test tools does this host have?
uv run tfqa quick-test --device /dev/sdX --dry-run
```

> **Status: alpha.** 23 commands, 350 tests, typed and linted in CI. The safety guardrails,
> dry-run previews, and JSON contract all do what they say. No engine reports a number it did
> not measure: one that cannot do real work refuses rather than estimating. Read
> [Safety](#safety) and [Known limitations](#known-limitations) before pointing this at a card
> you care about.

---

## Why this exists

Counterfeit SD cards are everywhere, and the existing tooling is scattered across platforms and
output formats. `f3probe` finds fake capacity but prints prose. `badblocks` scans surfaces but
knows nothing about card health. `fio` benchmarks but needs hand-written job files. H2testw and
CrystalDiskMark are Windows-only. None of them emit machine-readable results, so stitching them
into a repeatable QA process means writing throwaway parsers every time.

FlashCrucible wraps the good Linux tools behind one CLI with **one stable output contract**. Every
command returns the same JSON envelope, writes structured JSONL events, and appends to a run
history you can trend over time. It is built so a human and an AI agent can drive the same
commands and read the same results.

## What it does

| Command | What it does | Writes to device? |
| --- | --- | --- |
| `detect` | List block devices with size, bus, removability | No |
| `capabilities` | Probe which external tools are installed | No |
| `quick-test` | Fast counterfeit / capacity check via `f3probe` | **Yes** |
| `full-capacity-test` | Destructive full-span write + verify, detects wrapping fakes | **Yes** |
| `surface-scan` | Bad-block sweep via `badblocks` (required) | Only `--mode destructive` |
| `performance` | Throughput / latency / IOPS via `fio` (required) | **Yes** |
| `endurance` | Burn-in loop — **not implemented**, refuses to run | — |
| `workload-smallfiles` | Small-file create/read/delete metadata stress | **Yes** |
| `image-flash` | Write an image with `dd`, verify with `cmp` | **Yes** |
| `filesystem-check` | Run `fsck` against the filesystem | Only with `--force` |
| `health` | Read CID / wear registers (sysfs, `mmc extcsd`, `sdmon`) | No |
| `pipeline` | Run several stages as one orchestrated run | **Yes** |
| `status`, `cancel` | Follow or stop a background run | No |
| `combos`, `profiles` | List curated workflows and endurance presets | No |
| `describe`, `describe-schemas` | Machine-readable command and schema discovery | No |
| `validate-schemas`, `lint-schemas` | Check the shipped JSON schemas | No |
| `history`, `trends`, `report`, `automation-report` | Query and aggregate past runs | No |
| `config show`, `config validate` | Inspect the merged configuration | No |

Run `uv run tfqa --help` for the full list, or `uv run tfqa describe <command> --output json` for
a command's complete argument schema.

## Install

Requires **Python 3.13+** and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone git@github.com:grammy-jiang/FlashCrucible.git
cd FlashCrucible
uv sync
uv run tfqa --help
```

The external tools are optional — `tfqa capabilities` reports what is present and each command
degrades or errors clearly when its tool is missing.

```bash
# Debian / Ubuntu
sudo apt install f3 e2fsprogs fio mmc-utils

# Fedora / RHEL
sudo dnf install f3 e2fsprogs fio mmc-utils
```

## Quick tour

```bash
# 1. Find the card. Never assume a device path.
uv run tfqa detect

# 2. Check what this host can actually test with.
uv run tfqa capabilities

# 3. Preview a test without touching the card.
uv run tfqa quick-test --device /dev/sdX --dry-run

# 4. Run it for real (this writes to the card).
uv run tfqa quick-test --device /dev/sdX

# 5. Look at what past runs recorded.
uv run tfqa history
uv run tfqa trends --stage quick-test
```

Replace `/dev/sdX` with a real path from step 1. There is no default device, by design.

## Safety

**Never destructive by default, always explicit.**

- No command defaults to a device. You must pass `--device`.
- Every command that writes raw blocks calls `assert_safe_for_destructive()` from
  `tfqa/core/safety.py`, which refuses a device that is mounted or looks like a system disk.
- Overriding a refusal takes **both** `--force` and `--yes`. `--force` on its own is treated as an
  unconfirmed request, so a stray flag left in a script cannot arm a destructive run.

```bash
uv run tfqa quick-test --device /dev/sdX                 # refused if /dev/sdX is mounted
uv run tfqa --yes quick-test --device /dev/sdX --force   # explicit override
```

A refusal exits with code `3` and `error_code: DEVICE_UNSAFE`, and the payload names the reason
(`mountpoints`, `is_system_disk`) so automation can act on it.

Read-only paths stay usable on a mounted card: `surface-scan --mode readonly`, a `pipeline` whose
stages are all non-writing, `detect`, `health`, and every reporting command. `workload-smallfiles`
writes through a mounted filesystem, so it is intentionally exempt from the unmounted requirement.

### Dry runs

Every command that writes accepts `--dry-run`, as a global flag before the subcommand or as the
command's own flag after it. Both forms print the plan and execute nothing.

```bash
uv run tfqa --dry-run pipeline --device /dev/sdX --stages detect,quick-test
uv run tfqa quick-test --device /dev/sdX --dry-run
```

The plan carries a `safety` block reporting whether the real run would clear the guard, so you
can find out that a card is mounted without attempting the write:

```json
{
  "plan": {
    "device": "/dev/sdX",
    "stage_plan": ["detect", "quick-test"],
    "writes_to_device": true,
    "safety": {
      "would_run": false,
      "error_code": "DEVICE_UNSAFE",
      "reason": "has active mountpoints. Use --force --yes ...",
      "details": {
        "device_path": "/dev/sdX",
        "mountpoints": [{ "mountpoint": "/media/boot", "fstype": "vfat" }]
      }
    }
  }
}
```

The four `safety` keys are always present — on a clearing device they are
`{"would_run": true, "error_code": null, "reason": null, "details": {}}` — so automation never
has to handle two shapes.

Commands that never clear the unmounted requirement (`workload-smallfiles`,
`surface-scan --mode readonly`, read-only pipeline plans) omit the `safety` block rather than
predict a refusal that does not apply.

A dry run applies the same argument validation as a real run, so it will not hand back a plan the
real invocation would reject (`--mode typo`, `--duration 0`, `--file-count 0`, …).

### Long-running tests

`full-capacity-test` on a large card is hours of I/O. Start it detached and poll instead of
holding a connection open:

```bash
uv run tfqa --yes full-capacity-test --device /dev/sdX --force --detach
# {"run_id": "20260811T093157Z-3f9c1a20", "pid": 4321, "detached": true}

uv run tfqa status 20260811T093157Z-3f9c1a20
uv run tfqa status                    # every recent run
uv run tfqa cancel 20260811T093157Z-3f9c1a20
```

The run records phase, byte progress, and its outcome to a state file beside its JSONL log, so
`status` works from any process and a crashed run still leaves something readable. A run whose
process has vanished without recording an outcome is reported as `orphaned` rather than showing
progress that will never advance.

A run stopped mid-write leaves the device partially written. `wrote_to_device` records whether it
had started, and both `cancel` and `status` say so rather than leaving you to infer it.

## Automation and AI

Every command returns the same envelope, defined by `tfqa/data/schemas/json/cli_response.schema.json`:

```json
{
  "status": "ok",
  "command": "quick-test",
  "run_id": "...",
  "device": "/dev/sdX",
  "error_code": null,
  "message": "...",
  "data": { "metrics": {}, "details": {} },
  "log_path": "..."
}
```

```bash
TFQA_MODE=ai uv run tfqa detect     # shorthand for --output json --non-interactive --no-color
```

Discovery hooks let an agent learn the CLI without hardcoding it:

- `tfqa describe <command> --output json` — arguments, defaults, destructive flag and the
  conditions under which it applies, required and optional tools, and the `result_schema` that
  validates the command's `data` payload
- `tfqa describe-schemas --output json` — every shipped JSON schema with title and version
- `tfqa capabilities --output json` — which tools and features are available on this host

### MCP server

Agents can call the commands natively instead of shelling out and parsing stdout:

```bash
uv run tfqa mcp-server        # speaks MCP over stdio
```

```json
{
  "mcpServers": {
    "flashcrucible": { "command": "uv", "args": ["run", "tfqa", "mcp-server"] }
  }
}
```

Every command becomes one tool. Nothing is described twice: tool inputs are derived from
`describe`, and each tool's `outputSchema` **is** the shipped result schema, so a result that
validates against the tool contract is the same result the CLI promises.

The tools run the real CLI as a subprocess, which is the point — the safety guard, the exit codes,
and the envelope have one implementation rather than two. A destructive tool over MCP is exactly as
hard to fire as the same command in a shell: it refuses without both `force` and `yes`, and the
server never supplies either on the caller's behalf. Destructive tools carry `destructiveHint` and
say so in their description.

Long runs should pass `detach` and be polled with the `status` tool; a blocking call is bounded by
`TFQA_MCP_TIMEOUT` (default 600s) so one long test cannot wedge the server.

Exit codes are stable (`tfqa/core/error_codes.py`):

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Test ran and failed |
| `2` | Invalid arguments or configuration |
| `3` | Environment problem — missing tool, permissions, unsafe device, unimplemented engine |

## How it is organised

```mermaid
flowchart TD
    CLI["tfqa.cli<br/>Typer commands, human + JSON output"]
    ORCH["tfqa.orchestration<br/>pipelines, profiles, combos"]
    ENGINES["tfqa.tests<br/>capacity, surface, performance,<br/>endurance, workload, health"]
    EXT["tfqa.ext<br/>f3, badblocks, fio, mmc,<br/>sdmon, fsck, dd/image"]
    CORE["tfqa.core<br/>devices, config, logging,<br/>capabilities, safety, models"]
    REPORT["tfqa.reporting<br/>history, summary, trends"]

    CLI --> ORCH --> ENGINES --> EXT
    CLI --> ENGINES
    CLI --> CORE
    ENGINES --> CORE
    CLI --> REPORT
    ORCH --> REPORT
```

| Path | Contents |
| --- | --- |
| `tfqa/cli/main.py` | Every command, argument parsing, output rendering |
| `tfqa/core/` | Device detection, config merge, logging, capability probe, safety, Pydantic models |
| `tfqa/tests/` | Test engines, one package per category |
| `tfqa/ext/` | Thin wrappers around external binaries |
| `tfqa/orchestration/` | Pipeline sequencing, endurance profiles, workflow combos |
| `tfqa/reporting/` | Run history index, summaries, trend aggregation |
| `tfqa/data/schemas/json/` | 8 JSON schemas describing the output contract |
| `tfqa/data/profiles/`, `tfqa/data/workflows/` | Endurance presets and curated stage combos |
| `docs/` | Design notes, roadmap, tool study, UX requirements |

## Configuration

Precedence, lowest to highest:

```
defaults < /etc/tfqa/config.toml < ~/.config/tfqa/config.toml < ./tfqa.toml < TFQA_* env vars < CLI flags
```

See `examples/tfqa.toml` for a starting point, and `uv run tfqa config show` to see what the CLI
actually resolved.

Recognised environment variables: `TFQA_MODE`, `TFQA_LOG_DIR`, `TFQA_NON_INTERACTIVE`,
`TFQA_PROFILES_DIR`, `TFQA_SCHEMAS_DIR`, `TFQA_WORKFLOWS_DIR`.

## Development

```bash
make install     # create the venv and install dependencies
make verify      # everything CI runs
```

`make verify` covers lint, formatting, types, the tests, the same tests with every external tool
hidden, and the shipped JSON schemas. `make help` lists the individual targets if you want one on
its own; `make format` applies formatting and autofixes.

CI runs `make verify`, so there is one definition of "green" and a local run cannot drift from it.
See [CONTRIBUTING.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/CONTRIBUTING.md),
and [AGENTS.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/AGENTS.md) if you are an
AI agent working in this repository.

The suite never touches real hardware. CLI tests use Typer's `CliRunner` with
`unittest.mock.patch` to stub device access, and the engine tests write to temporary files standing
in for block devices. `make test-hermetic` runs it again with every external binary hidden from
`shutil.which`, so a missing `f3probe` or `fio` cannot make a test pass or fail by accident.

## Known limitations

Honest list of what is constrained. Contributions welcome.

1. **`endurance` is not implemented.** It performed no device I/O and reported invented figures,
   so it now refuses with `NOT_IMPLEMENTED` rather than lying. Use `quick-test` or
   `full-capacity-test` for real measurements. Implementing it properly makes it a genuinely
   long-running command, which needs
   [#17](https://github.com/grammy-jiang/FlashCrucible/issues/17) first.
2. **Some commands need their tool present.** `performance` requires `fio` and `surface-scan`
   requires `badblocks`; without them they report the tool as missing instead of estimating.
   `surface-scan` reports the bad-block count badblocks actually found, and no latency figure,
   because badblocks does not measure one.
   Run `tfqa capabilities` to see what this host can do. In a `pipeline` an unavailable stage is
   recorded as `skipped`, so one missing tool does not fail an otherwise good run.
3. **Health data needs the right hardware.** Wear data comes from eMMC `EXT_CSD` registers
   (usually root) or from `sdmon` on industrial SD cards. A consumer card in a USB reader can
   supply neither, so `tfqa health` reports what is unavailable and why rather than guessing.
4. **A wrapping counterfeit's real size needs `quick-test`.** `full-capacity-test` detects the
   wrap and says so, but recovering the true capacity takes f3probe's binary search, which
   `tfqa quick-test` runs. The full test only reports a real size when the device stopped
   accepting writes, where the answer is unambiguous.
5. **Raw device access needs root.** `full-capacity-test`, `surface-scan --mode destructive`,
   `image-flash`, and `mmc extcsd read` all need write or ioctl access to the block device.

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/cli-guide.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/cli-guide.md) | Detailed notes on individual commands, payloads, and automation hooks |
| [docs/tool-wrapping-strategy.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/tool-wrapping-strategy.md) | Which external tools are wrapped, and what must be built natively |
| [docs/design-v0-structure.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/design-v0-structure.md) | Module and package architecture |
| [docs/design-v0-details.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/design-v0-details.md) | Detailed design notes |
| [docs/features-roadmap-v0.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/features-roadmap-v0.md) | Feature priorities and phase plan |
| [docs/sd-tools-study-v0.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/sd-tools-study-v0.md) | Survey of existing microSD test tools |
| [docs/ux-v0.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/ux-v0.md) | CLI UX requirements |
| [docs/phase3-4-plan.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/phase3-4-plan.md) | Endurance, orchestration, and automation work plan |
| [docs/agent-readiness.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/docs/agent-readiness.md) | What works today for AI agents, and what is missing |
| [AGENTS.md](https://github.com/grammy-jiang/FlashCrucible/blob/master/AGENTS.md) | Working agreement for AI agents contributing to this repository |

## Platform support

Linux only: x86_64, arm32, arm64. Tested against Debian/Ubuntu and Fedora/CentOS/RHEL families.

The Linux-only scope is deliberate rather than incidental: the tool reads card identity from
sysfs, wear registers through the MMC ioctl interface, and drops the page cache with
`posix_fadvise`. None of those have equivalents on Windows or macOS.

## License

[MIT](https://github.com/grammy-jiang/FlashCrucible/blob/master/LICENSE) © Grammy Jiang
