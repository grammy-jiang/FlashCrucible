"""Prove the safety-critical predicates are actually guarded.

A test that passes both before and after a fix proves nothing, and a guard with
no test behind it is indistinguishable from one with a test that never fails.
Rule 8 of AGENTS.md asks for this by hand; here it is asked of the suite.

Each mutant breaks one predicate and names the tests that must go red. If they
stay green, the guard exists only in the source.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.mutations import MUTANTS

#: Enough to prove the guard bites without paying for the whole file. `-x`
#: stops at the first failure, which is all the proof needed and keeps the
#: harness quick.
TIMEOUT_SECONDS = 180


def _pytest(
    selection: tuple[str, ...], mutate: str | None
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a child, mutated or explicitly not.

    The variable is always set or always removed, never inherited: a developer
    with `TFQA_MUTATE` exported would otherwise see the unmutated check run
    mutated and fail for the wrong reason.
    """

    environment = {**os.environ}
    if mutate:
        environment["TFQA_MUTATE"] = mutate
    else:
        environment.pop("TFQA_MUTATE", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-x",
            "-q",
            "--no-header",
            # No cache writes from a child run inside another test run.
            "-p",
            "no:cacheprovider",
            *selection,
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_breaking_the_predicate_breaks_the_suite(name: str) -> None:
    mutant = MUTANTS[name]
    result = _pytest(mutant.guarded_by, name)

    assert result.returncode != 0, (
        f"Breaking {mutant.target} changed nothing: {', '.join(mutant.guarded_by)} "
        f"still passed. Consequence if it regresses: {mutant.consequence}"
    )


class TestTheHarnessItself:
    """A harness that cannot fail is the thing it was built to catch."""

    def test_the_guarded_tests_pass_unmutated(self) -> None:
        # Otherwise a mutant would "pass" because the tests were already red.
        files = tuple(sorted({path for m in MUTANTS.values() for path in m.guarded_by}))
        result = _pytest(files, None)
        assert result.returncode == 0, result.stdout[-2000:]

    def test_every_mutant_names_files_that_exist(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        missing = [
            path
            for mutant in MUTANTS.values()
            for path in mutant.guarded_by
            if not (root / path.split("::")[0]).is_file()
        ]
        assert not missing, f"guarded_by names nothing: {missing}"

    def test_every_mutant_targets_something_real(self) -> None:
        from tests.mutations import _resolve

        for name, mutant in MUTANTS.items():
            module, attribute = _resolve(mutant.target)
            assert hasattr(module, attribute), f"{name}: {mutant.target} is gone"

    def test_every_mutant_says_what_goes_wrong(self) -> None:
        # The consequence is the reason the predicate is on this list; without
        # it the next person cannot judge whether to keep it.
        assert all(m.consequence.strip() for m in MUTANTS.values())

    def test_an_unknown_mutant_is_an_error(self) -> None:
        from tests import mutations

        with pytest.raises(KeyError):
            mutations.apply("no-such-mutant")
