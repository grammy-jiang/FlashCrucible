"""Deliberate breakages of the predicates that must never regress silently.

Rule 8 of AGENTS.md -- "a regression test must fail against the old code" -- has
repeatedly caught fixes that did not do what they claimed, but only because
someone remembered to check. This turns the check for the handful of predicates
where a silent regression is expensive into something the suite runs.

Adding one is a single entry: what to break, and which tests must notice. This
is deliberately *not* whole-codebase mutation testing, which is slow, noisy, and
mostly generates mutants nobody cares about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Mutant:
    """A predicate broken on purpose, and the tests that must catch it."""

    #: `module.attribute` to replace, resolved at run time.
    target: str
    #: Stands in for the real implementation.
    replacement: Callable[..., Any]
    #: Test files or node ids expected to fail once the mutation is applied.
    guarded_by: tuple[str, ...]
    #: What goes wrong in the field if nothing catches it.
    consequence: str


def _normalize_status_defaulting_to_ok(raw_status: Any | None = None) -> str:
    """`normalize_status` with only its unknown-value fallback reversed.

    The mapping of every recognised value is kept, so the one test this mutant
    must break is the one asserting an unrecognised value becomes "error".
    """

    from tfqa.orchestration import pipeline

    if raw_status is None:
        return "ok"
    candidate = str(raw_status).lower().strip()
    candidate = pipeline._STATUS_SYNONYMS.get(candidate, candidate)
    if candidate in pipeline._VALID_STATUSES:
        return candidate
    return "ok"  # the regression: "we do not know that it succeeded"


def _resolve(target: str) -> tuple[Any, str]:
    module_path, _, attribute = target.rpartition(".")
    module = __import__(module_path, fromlist=[attribute])
    return module, attribute


MUTANTS: dict[str, Mutant] = {
    "safety-guard-never-refuses": Mutant(
        target="tfqa.core.safety.assert_safe_for_destructive",
        replacement=lambda *args, **kwargs: None,
        guarded_by=("tests/test_cli_safety_guard.py",),
        consequence="A mounted card or the system disk gets overwritten.",
    ),
    "dry-run-ignored": Mutant(
        target="tfqa.cli.main._resolve_dry_run",
        # The global `--dry-run` was once parsed and stored but never read, so
        # `tfqa --dry-run <destructive command>` executed for real. It did.
        replacement=lambda ctx, explicit: False,
        guarded_by=("tests/test_cli_dry_run.py",),
        consequence="`--dry-run` writes to the device it promised not to touch.",
    ),
    "unknown-status-becomes-ok": Mutant(
        target="tfqa.orchestration.pipeline.normalize_status",
        # Only the unknown branch changes. Mapping *everything* to "ok" would
        # be caught by the canonical-value tests, so the harness would report
        # this predicate as guarded even if the assertion that actually
        # matters -- unknown becomes "error" -- were deleted.
        replacement=_normalize_status_defaulting_to_ok,
        guarded_by=(
            "tests/test_orchestration_pipeline.py::TestNormalizeStatus"
            "::test_unknown_status_is_an_error_not_a_pass",
        ),
        consequence=(
            "A counterfeit detected inside a pipeline is recorded as a passing "
            "stage. This was real, and shipped."
        ),
    ),
    "wrap-detection-blinded": Mutant(
        target="tfqa.core.blockio.decode_offset",
        # A wrapping counterfeit is only distinguishable from ordinary
        # corruption by the offset the block claims to hold. Patched in
        # `blockio`, which is where the verify pass -- and the wrap test --
        # now lives for every engine.
        replacement=lambda block, span=None: None,
        # The node id, not the file: with `-x` a file-level selection stops at
        # the unit test of the helper, which proves less than the behaviour.
        guarded_by=(
            "tests/test_capacity_full.py::CounterfeitDevice"
            "::test_wrapping_writes_are_detected",
        ),
        consequence="A fake-capacity card is reported as merely corrupt.",
    ),
    "endurance-reports-a-run-it-did-not-do": Mutant(
        target="tfqa.tests.endurance.simple.run_simple_endurance",
        # What the engine used to do: return a plausible-looking result for a
        # run that never happened, including for a device it could not test.
        replacement=lambda ctx, config, progress=None: {
            "name": "endurance.simple",
            "status": "ok",
            "metrics": {"passes_completed": 100, "bytes_written": 1 << 30},
        },
        guarded_by=("tests/test_engines_do_not_invent.py",),
        consequence=(
            "Endurance reports passes it never ran, and `trends` averages the "
            "numbers into a history. This shipped once."
        ),
    ),
}


def apply(name: str) -> None:
    """Break one predicate, permanently, for this process."""

    mutant = MUTANTS[name]
    module, attribute = _resolve(mutant.target)
    setattr(module, attribute, mutant.replacement)
