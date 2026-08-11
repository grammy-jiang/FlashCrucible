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

#### A. Invariant tests over the command surface — *highest value*

Several rules in AGENTS.md are currently enforced only by an agent remembering to read them. They
can be enforced mechanically by introspecting the Typer command tree:

1. **Every write-capable command calls the safety guard.** Would have caught the original
   unguarded `quick-test`, `image-flash`, `surface-scan`, `performance`, `endurance`, and
   `pipeline`.
2. **Every write-capable command honours both `--dry-run` spellings.**
3. **No engine returns a status outside the pipeline vocabulary.** Would have caught the
   `"fail"` → `"ok"` mapping directly.
4. **Every command's `describe` output declares its destructive flag correctly.**

Roughly 4 of the 24 findings become impossible rather than merely discouraged. The list of
write-capable commands should be derived, not hand-maintained, or it becomes another thing to
forget.

*Effort: medium. Value: the highest of anything here.*

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

#### E. Per-command result schemas — *the biggest real gap*

`cli_response.schema.json` constrains the envelope but leaves `data` as a free-form object. So an
agent can validate that a response *is* a `CLIResponse`, but **cannot validate a `quick-test`
result**, and cannot know which keys to expect without reading the source.

What is needed:

- a result schema per command, alongside the existing eight;
- `describe <command>` gaining a `result_schema` pointer;
- `validate-schemas` covering the new files, which it already would.

This is what turns "parse the JSON and hope" into a contract. Note that some of it exists
implicitly — `summary.schema.json` and `trends.schema.json` already do this for two commands, so
the pattern is established rather than novel.

*Effort: medium. Value: highest on the usage side.*

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

#### F2. Stop the performance fallback inventing throughput — *known defect*

When `fio` is absent, both performance engines return figures derived from device properties
rather than measurement: `basic.py` uses a flat `240.0` for removable devices, `random.py`
computes one from block size and queue depth. Both return `status: "ok"` and place the numbers in
`metrics`, which is what `trends` aggregates. The `mode: "simulated"` marker sits in `details`,
which `trends` never reads.

So a 240 MB/s figure that was never measured can appear in a throughput trend, indistinguishable
from a real one. This is the same defect that was removed from the health readers, surviving in a
module the health work did not touch — found by an AI reviewer checking a README claim against the
code rather than against the documentation.

Options, in order of preference:

1. Raise `ToolNotFoundError` and let `performance` report the measurement as unavailable, matching
   what `health` now does.
2. Keep a clearly-labelled estimate, but move the marker somewhere `trends` respects and exclude
   simulated runs from aggregation.

Option 1 is consistent with the rest of the project. Either way the fix belongs with **E**, since
the result schema should make "measured" versus "estimated" explicit rather than leaving it to a
key in `details`.

*Effort: small. Value: high — it is a correctness bug in output people make decisions from.*

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

| Phase | Items | Rationale |
| --- | --- | --- |
| 0 | **F2** | A live correctness bug in output people act on; small |
| 1 | **B**, **C**, **G** | Small, independent, immediately useful |
| 2 | **A** | Turns the AGENTS.md rules into checks; highest development-side value |
| 3 | **E** | Makes the output contract complete and verifiable |
| 4 | **F** | Fixes the synchronous-only limitation |
| 5 | **H** | Only once E and F are done |
| — | **D** | Optional; worth it only for the critical predicates |

## What not to do

- **Do not write more instructions.** The 34 KB Copilot file described a safety model the code did
  not implement. Length was not the problem; the absence of enforcement was. Prefer a check over a
  paragraph.
- **Do not add an MCP server before the contract is complete.** See H.
- **Do not soften the safety guards for agent convenience.** An agent that cannot run
  `full-capacity-test` on a mounted device is the guard working. The correct fix is a clearer
  error, not a wider default.
