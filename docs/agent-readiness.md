# Agent readiness

What works today for AI agents, what is missing, and what is worth building next. Two audiences,
which need different things:

- **Agents developing FlashCrucible** — writing code in this repository.
- **Agents using FlashCrucible** — driving `tfqa` to test cards and act on the results.

The working agreement for the first group lives in [AGENTS.md](../AGENTS.md). This document is the
gap analysis behind it.

---

## The premise

FlashCrucible has been through a review cycle where two independent AI reviewers raised **24
findings** across six pull requests. Every one was a real defect. That sample is worth taking
seriously, because it says something specific about what helps.

Of the 24:

- **2 were regressions introduced while fixing something else** — a guard bypassed by the very
  change that added the guard, and undefined registers counting as health data in the change whose
  purpose was to stop fabricating health data.
- **1 was pre-existing and severe**: `normalize_status()` mapped any unrecognised status to `"ok"`,
  while every engine reports `"fail"`. Counterfeits detected inside a pipeline had always been
  recorded as passing stages.
- The rest were narrower: swallowed errors, unvalidated inputs, misleading labels, host-dependent
  tests.

None of them were prevented by documentation. The repository already had a 34 KB instruction file
saying "never destructive by default" while the safety guard was wired into nothing. **Prose did
not hold; executable checks did.** That observation drives the priorities below.

---

## Part 1 — Agents developing FlashCrucible

### What exists

| Capability | State |
| --- | --- |
| Fast, hermetic test suite | 324 tests, ~1.5 s, no hardware, passes with all external tools hidden |
| Types and lint enforced in CI | `ruff`, `ruff format --check`, `mypy tfqa/ tests/` |
| Schema self-validation | `tfqa validate-schemas`, `tfqa lint-schemas` |
| Working agreement | [AGENTS.md](../AGENTS.md) |

The suite is the strongest asset here. It is fast enough to run on every edit, and hermetic enough
that a pass means something.

### What is missing

#### A. Invariant tests over the command surface — *done*

Resolved in [#15](https://github.com/grammy-jiang/FlashCrucible/issues/15).
`tests/test_command_surface_invariants.py` reads the command surface out of the Typer tree and
enforces four of the AGENTS.md rules mechanically: anything taking `--device` clears the safety
guard and supports `--dry-run` (or is exempt for a recorded reason), no engine reports a status
the pipeline vocabulary would reject, and `describe` agrees with the code about what is
destructive.

It found a live problem on its first run: `quick-test`, `surface-scan`, `filesystem-check`,
`performance`, and `pipeline` all reported `destructive: false` while calling the safety guard.
`quick-test` is the command that wrote to a mounted card at the start of this work — an agent
reading `describe` would have been told it was harmless. They now declare `destructive: true`
alongside a `destructive_when` string, since several are only destructive with a particular flag
and a bare boolean cannot say which.

Verified by adding an unguarded `--device` command and watching three checks fail.

#### B. Hermetic-tools CI job

The "hide all 16 external binaries" run has been executed by hand on every pull request in this
series, and it caught a real host dependency that CI missed. It is currently one person's
discipline. It should be a CI job.

*Effort: small. Value: high — it already caught something.*

#### C. One-command gate

Five separate commands must pass before a PR. Agents get the set subtly wrong — running
`ruff format` instead of `ruff format --check`, or skipping schema validation. A single
`make verify` (or `uv run poe verify`) removes the guesswork and gives CI and local runs one
definition.

*Effort: small. Value: moderate, mostly in reduced friction.*

#### D. Mutation-style spot checks

Rule 8 in AGENTS.md — "a regression test must fail against the old code" — is currently manual.
For the safety-critical paths specifically (the guard, `--dry-run` short-circuits,
`normalize_status`), a small harness that flips the condition and asserts the suite goes red would
turn that discipline into a check.

*Effort: medium. Value: moderate. Worth doing only for the handful of genuinely critical
predicates, not the whole codebase.*

---

## Part 2 — Agents using FlashCrucible

### What exists

This side is further along than most CLIs, and it is the project's stated design goal.

| Capability | State |
| --- | --- |
| Uniform response envelope | Every command returns `CLIResponse`; schema shipped |
| Stable exit codes | `0` ok, `1` test failed, `2` bad arguments, `3` environment |
| Command discovery | `tfqa describe <command> --output json` — arguments, defaults, destructive flag |
| Schema discovery | `tfqa describe-schemas --output json` — 8 schemas with titles and versions |
| Host capability probe | `tfqa capabilities --output json` |
| Agent mode | `TFQA_MODE=ai` → JSON, non-interactive, no colour |
| Safe preview | `--dry-run` on every writing command, with a `safety` block predicting refusal |
| Structured history | JSONL events, run history index, `trends` aggregation |

An agent can already discover the command set, check what the host supports, preview a destructive
action including whether it would be refused, and parse every result the same way. That is a
genuinely good baseline.

### What is missing

#### E. Per-command result schemas — *done*

Resolved in [#16](https://github.com/grammy-jiang/FlashCrucible/issues/16). Twenty-four result
schemas now ship alongside the envelope, `describe` carries a `result_schema` pointer, and
`tests/test_result_schemas.py` validates the **real output of every command** against its schema
rather than only checking that the files parse -- a schema nobody validates against drifts.

Each schema validates the whole `CLIResponse` and conditions the shape of `data` on `status`,
because the payload legitimately differs between a success, a dry run, and an error. A first
attempt constrained `data` alone with nothing required, so `{}` validated as a successful
`quick-test` result and three schemas described a layout the commands never emitted -- the tests
passed because the schemas asserted almost nothing.

It found a contract inconsistency immediately: `workload-smallfiles` named the target
`device_path` in its dry-run plan while every other command used `device`, so a caller could not
read the target out of a plan uniformly. `_emit_dry_run` now guarantees the key rather than
relying on each command to remember it.

The tests also check the schemas *constrain* something -- one that accepts anything is worse than
none, because it implies a check that is not happening -- and that no schema file describes a
command which no longer exists.

#### F. Long-running operations — *a gap the recent work created*

`full-capacity-test` on a 128 GB card is hours of I/O. `surface-scan` and `endurance` are worse.
Every command is currently synchronous, so an agent calling one either blocks past any sane tool
timeout or kills it mid-write.

This mattered less when `full-capacity-test` was a stub returning instantly. Now that it does real
work, it is a live problem.

What is needed:

- start a run detached, returning a `run_id` immediately;
- `tfqa status <run_id>` returning progress, phase, and partial metrics;
- the engines already accept a `progress` callback, so the plumbing exists;
- a documented cancellation path that leaves the device in a known state.

*Effort: medium-to-large. Value: high — without it, the agent story breaks on exactly the commands
that take real time.*

#### F2. Stop the engines inventing measurements — *done*

Resolved in [#11](https://github.com/grammy-jiang/FlashCrucible/issues/11). The scope turned out
to be four engines, not one: `performance/basic`, `performance/random`, `surface/scan`, and
`endurance/simple`. All of them wrote synthesised figures into `metrics`, which is what `trends`
aggregates, while any marker sat in `details`, which it never reads. `endurance` was the worst —
it did no device I/O whatsoever and reported "58 TB written, 0 errors" against a device path that
did not exist.

Engines now refuse: `ToolNotFoundError` propagates from `performance` and `surface-scan`, and
`endurance` raises `NotImplementedEngineError`. Pipelines record an unavailable stage as
`skipped`, so it contributes no metrics and does not fail the run.

The lesson is recorded here because it recurred: this was the *same* defect removed from the
health readers in #8, surviving in modules that work never touched. A claim about the codebase is
worth checking against the codebase, not against the module you just fixed.

#### G. Tool requirements in `describe`

`capabilities` reports that `fio` is missing. `describe performance` does not mention that
`performance` degrades to a synthetic benchmark without it. The agent must know the relationship
independently, which means hardcoding what the CLI already knows.

Each command should declare its required and optional tools and what happens when they are absent.
Small change, removes a class of guesswork.

*Effort: small. Value: moderate.*

#### H. MCP server

Exposing the CLI as MCP tools would let agents call FlashCrucible natively instead of shelling out
and parsing. It is the most visible "AI-native" move available.

It is also the one most likely to be built too early. An MCP server is a thin projection of the
underlying contract: without **E** the tool outputs are unvalidatable, and without **F** every
long-running tool call times out. Building it first would bake both gaps into a second interface.

*Effort: large. Value: high, but only after E and F. Do not start here.*

---

## Suggested order

Each item below is tracked as a GitHub issue, collected under
[#20](https://github.com/grammy-jiang/FlashCrucible/issues/20).

| Phase | Item | Issue | Rationale |
| --- | --- | --- | --- |
| ~~0~~ | ~~**F2**~~ | [#11](https://github.com/grammy-jiang/FlashCrucible/issues/11) | **Done** — four engines, not one |
| ~~1~~ | ~~**B**~~ | [#12](https://github.com/grammy-jiang/FlashCrucible/issues/12) | **Done** — Currently run by hand; already caught a real host dependency |
| ~~1~~ | ~~**C**~~ | [#13](https://github.com/grammy-jiang/FlashCrucible/issues/13) | **Done** — Removes a recurring source of avoidable CI failures |
| ~~1~~ | ~~**G**~~ | [#14](https://github.com/grammy-jiang/FlashCrucible/issues/14) | **Done** — Small; removes guesswork for callers |
| ~~2~~ | ~~**A**~~ | [#15](https://github.com/grammy-jiang/FlashCrucible/issues/15) | **Done** — found five mislabelled commands on its first run |
| ~~3~~ | ~~**E**~~ | [#16](https://github.com/grammy-jiang/FlashCrucible/issues/16) | **Done** — found a plan-key inconsistency on its first run |
| 4 | **F** | [#17](https://github.com/grammy-jiang/FlashCrucible/issues/17) | Fixes the synchronous-only limitation |
| 5 | **H** | [#18](https://github.com/grammy-jiang/FlashCrucible/issues/18) | Only once E and F are done |
| — | **D** | [#19](https://github.com/grammy-jiang/FlashCrucible/issues/19) | Optional; worth it only for the critical predicates |

Phase 1 items are independent of each other and can be done in any order.

## What not to do

- **Do not write more instructions.** The 34 KB Copilot file described a safety model the code did
  not implement. Length was not the problem; the absence of enforcement was. Prefer a check over a
  paragraph.
- **Do not add an MCP server before the contract is complete.** See H.
- **Do not soften the safety guards for agent convenience.** An agent that cannot run
  `full-capacity-test` on a mounted device is the guard working. The correct fix is a clearer
  error, not a wider default.
