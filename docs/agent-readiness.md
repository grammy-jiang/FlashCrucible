# Agent readiness

What works today for AI agents, what is missing, and what is worth building next. Two audiences,
which need different things:

- **Agents developing FlashCrucible** — writing code in this repository.
- **Agents using FlashCrucible** — driving `tfqa` to test cards and act on the results.

The working agreement for the first group lives in [AGENTS.md](../AGENTS.md). This document is the
gap analysis behind it.

**Status: every item identified here is done** ([#11](https://github.com/grammy-jiang/FlashCrucible/issues/11)–[#19](https://github.com/grammy-jiang/FlashCrucible/issues/19)).
It is kept as the reasoning, not as a to-do list — in particular the premise below, which is why
the work was ordered the way it was.

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

### What was missing

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

#### B. Hermetic-tools run — *done*

Resolved in [#12](https://github.com/grammy-jiang/FlashCrucible/issues/12). `pytest --hermetic`
hides all 17 external binaries from `shutil.which`, and it runs in CI as part of `make verify`
rather than as a separate job — one target means the hermetic run cannot be skipped without
skipping everything.

The run had been executed by hand on every pull request in this series, and it caught a real host
dependency CI missed: three CLI tests passed only because `f3probe` happened to be installed
locally. That it depended on one person remembering is exactly why it is now a target.

#### C. One-command gate — *done*

Resolved in [#13](https://github.com/grammy-jiang/FlashCrucible/issues/13). `make verify` runs
lint, formatting, types, the tests, the hermetic tests, and the schema checks, and it is what CI
runs — so the two cannot drift.

Five separate commands used to be required before a PR, listed in four places that disagreed.
Agents got the set subtly wrong: `ruff format` instead of `ruff format --check`, or skipping
schema validation, and found out from CI.

#### D. Mutation-style spot checks — *done*

Rule 8 in AGENTS.md — "a regression test must fail against the old code" — was manual, and for the
safety-critical paths that discipline is now a check.

Resolved in [#19](https://github.com/grammy-jiang/FlashCrucible/issues/19). `tests/mutations.py`
lists five predicates — the safety guard, the `--dry-run` short-circuit, `normalize_status`, wrap
detection, and an unimplemented engine refusing rather than inventing a result — and
`tests/test_mutation_guards.py` breaks each one and asserts the named tests go red. Adding one is a
single dictionary entry. It runs in about three seconds.

Deliberately not whole-codebase mutation testing: slow, noisy, and most mutants are uninteresting.

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

### What was missing

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

#### F. Long-running operations — *done*

Resolved in [#17](https://github.com/grammy-jiang/FlashCrucible/issues/17). `--detach` starts a run
in a new session and returns its `run_id` immediately; `tfqa status` reports phase, byte progress,
and outcome; `tfqa cancel` stops it.

State lives in a small JSON file beside the run's JSONL log rather than in a daemon, so it is
readable from any process and a crashed run still leaves something behind. Writes are atomic, so a
poller never sees a half-written file. A run marked running whose process has vanished is reported
as `orphaned` rather than showing progress that will never advance.

Cancellation is honest about consequences: a run stopped mid-write leaves the device partially
written, and `wrote_to_device` records whether it had started, so neither `cancel` nor `status`
leaves that to be inferred.

#### F2. Stop the engines inventing measurements — *done*

Resolved in [#11](https://github.com/grammy-jiang/FlashCrucible/issues/11). The scope turned out
to be four engines, not one: `performance/basic`, `performance/random`, `surface/scan`, and
`endurance/simple`. All of them wrote synthesised figures into `metrics`, which is what `trends`
aggregates, while any marker sat in `details`, which it never reads. `endurance` was the worst —
it did no device I/O whatsoever and reported "58 TB written, 0 errors" against a device path that
did not exist.

Engines now refuse: `ToolNotFoundError` propagates from `performance` and `surface-scan`.
Pipelines record an unavailable stage as `skipped`, so it contributes no metrics and does not fail
the run.

`endurance` refused outright until it could measure for real, which it now does
([#32](https://github.com/grammy-jiang/FlashCrucible/issues/32)) — writing and verifying the span
once per pass and reporting how the numbers move. What it still will not do is estimate: no
lifetime, no TBW remaining, no health score, and wear only when the card's own registers answer.

The lesson is recorded here because it recurred: this was the *same* defect removed from the
health readers in #8, surviving in modules that work never touched. A claim about the codebase is
worth checking against the codebase, not against the module you just fixed.

#### G. Tool requirements in `describe` — *done*

Resolved in [#14](https://github.com/grammy-jiang/FlashCrucible/issues/14). `describe` reports
`required_tools`, `optional_tools` and `degradation` per command, and the MCP tool descriptions are
generated from them.

Before it, `capabilities` would report `fio` missing while `describe performance` said nothing
about what `performance` does without it, so a caller had to know the relationship independently —
hardcoding what the CLI already knew.

#### H. MCP server — *done*

Exposing the CLI as MCP tools lets agents call FlashCrucible natively instead of shelling out and
parsing. It was the most visible "AI-native" move available, and the one most likely to be built
too early: an MCP server is a thin projection of the underlying contract, so without **E** its
outputs would have been unvalidatable and without **F** every long call would have timed out.
Building it first would have baked both gaps into a second interface.

Resolved in [#18](https://github.com/grammy-jiang/FlashCrucible/issues/18), after E and F, as
`tfqa mcp-server`. It held to the projection: tool inputs come from `describe`, outputs are the
shipped result schemas, and each call runs the real CLI as a subprocess so the safety guard has
one implementation rather than two.

It held to the projection, and that is the property to defend: the moment it starts deciding
things for itself there are two implementations of the safety model.

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
| ~~4~~ | ~~**F**~~ | [#17](https://github.com/grammy-jiang/FlashCrucible/issues/17) | **Done** — detach, status, cancel |
| ~~5~~ | ~~**H**~~ | [#18](https://github.com/grammy-jiang/FlashCrucible/issues/18) | **Done** — `tfqa mcp-server`, built after E and F as planned |
| ~~—~~ | ~~**D**~~ | [#19](https://github.com/grammy-jiang/FlashCrucible/issues/19) | **Done** — five predicates, ~3s |

Every item is done. What follows is kept because the reasoning still applies to the next thing
someone is tempted to add.

## What not to do

- **Do not write more instructions.** The 34 KB Copilot file described a safety model the code did
  not implement. Length was not the problem; the absence of enforcement was. Prefer a check over a
  paragraph.
- **Do not let the MCP server grow its own logic.** It is a projection of the CLI; the moment it
  decides anything about safety or output shape there are two implementations, and only one of
  them is tested. See H.
- **Do not soften the safety guards for agent convenience.** An agent that cannot run
  `full-capacity-test` on a mounted device is the guard working. The correct fix is a clearer
  error, not a wider default.
