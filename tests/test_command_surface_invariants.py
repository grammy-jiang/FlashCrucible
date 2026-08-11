"""Structural invariants over the CLI command surface.

Two AI reviewers raised 24 findings across #4-#9. None were prevented by
documentation -- the repository already carried an instruction file describing
guards that were wired into nothing. These tests enforce four of those rules
mechanically instead, so a new command cannot quietly skip them:

1. anything taking `--device` clears the safety guard, or is exempt for a
   recorded reason;
2. anything taking `--device` previews with `--dry-run` and returns before
   doing any work, on the same terms;
3. no engine reports a status the pipeline vocabulary would not recognise;
4. `describe` agrees with the code about which commands are destructive.

Everything is derived from the live Typer registry, never from a list kept
here: the commands, their options, and the engines the pipeline actually calls.
Only the *exemptions* are written down, and each carries its reason.

An earlier version discovered commands by pattern-matching decorators in the
module AST, which missed `@app.command()` without an explicit name, a name
given as a constant, and keyword-only parameters -- so a command could evade
the very checks meant to be inescapable. Discovery now starts from the registry
and asserts it accounted for every registered command.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Callable, NamedTuple

import pytest
from typer.testing import CliRunner

from tfqa.cli.main import _TOOL_REQUIREMENTS, _collect_command_map, app
from tfqa.core.models import CLIResponse
from tfqa.orchestration import pipeline as pipeline_mod
from tfqa.orchestration.pipeline import _STATUS_SYNONYMS, _VALID_STATUSES

DEVICE_FLAG = "--device"
DRY_RUN_FLAG = "--dry-run"

# A command may skip the safety guard only for a reason recorded here. Adding a
# device-touching command without either the guard or an entry fails the build.
GUARD_EXEMPT = {
    "health": "reads identity and wear registers; writes nothing",
    "history": "queries the run history; --device is a filter, not a target",
    "workload-smallfiles": (
        "writes through a mounted filesystem, so requiring an unmounted device "
        "would make it impossible to run"
    ),
    "endurance": (
        "performs no device I/O while unimplemented; guarding it would answer "
        "DEVICE_UNSAFE instead of NOT_IMPLEMENTED"
    ),
}

# Likewise for --dry-run: only commands that do no work are excused.
DRY_RUN_EXEMPT = {
    "health": "read-only; there is nothing to preview",
    "history": "a query, not an operation",
}


class Command(NamedTuple):
    name: str
    callback: Callable[..., object]
    flags: frozenset[str]
    calls: frozenset[str]
    dry_run_returns: bool

    @property
    def takes_device(self) -> bool:
        return DEVICE_FLAG in self.flags

    @property
    def calls_guard(self) -> bool:
        return "_assert_device_safe" in self.calls

    @property
    def previews(self) -> bool:
        """Resolves the flag, emits a plan, and returns before doing work."""
        return (
            "_resolve_dry_run" in self.calls
            and "_emit_dry_run" in self.calls
            and self.dry_run_returns
        )


def _called_names(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _dry_run_branch_returns(tree: ast.AST) -> bool:
    """True when an `if _resolve_dry_run(...)` branch emits a plan and returns.

    Calling the resolver is not enough: a command could ignore the result, skip
    the emitter, or fall through into the write afterwards, and still look
    compliant.
    """

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "_resolve_dry_run" not in _called_names(node.test):
            continue
        body = ast.Module(body=node.body, type_ignores=[])
        if "_emit_dry_run" not in _called_names(body):
            continue
        if any(isinstance(inner, ast.Return) for inner in ast.walk(body)):
            return True
    return False


def _commands() -> list[Command]:
    """Read the command surface out of the live Typer registry."""

    found: list[Command] = []
    for name, command in _collect_command_map().items():
        callback = command.callback
        if callback is None:  # pragma: no cover - groups have no callback
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(callback)))
        flags = {opt for param in command.params for opt in getattr(param, "opts", [])}
        found.append(
            Command(
                name=name,
                callback=callback,
                flags=frozenset(flags),
                calls=frozenset(_called_names(tree)),
                dry_run_returns=_dry_run_branch_returns(tree),
            )
        )
    return found


ALL_COMMANDS = _commands()
DEVICE_COMMANDS = [c for c in ALL_COMMANDS if c.takes_device]

_runner = CliRunner()


def _describe(command: str) -> dict[str, object]:
    result = _runner.invoke(app, ["describe", command, "--output", "json"])
    assert result.exit_code == 0, result.stdout
    return dict(CLIResponse.model_validate_json(result.stdout).data["describe"])


class TestDiscoveryIsComplete:
    """If discovery misses a command, every check below passes vacuously."""

    def test_every_registered_command_was_examined(self) -> None:
        # Groups (`config`) have no callback of their own; their subcommands
        # are registered separately and are examined.
        registered = {
            name
            for name, command in _collect_command_map().items()
            if command.callback is not None
        }
        assert {c.name for c in ALL_COMMANDS} == registered

    def test_device_commands_were_found(self) -> None:
        assert DEVICE_COMMANDS

    def test_discovery_matches_the_registry_option_by_option(self) -> None:
        # Flags come from Click, so keyword-only and positional-only parameters
        # are handled the same way Typer handles them.
        for command in DEVICE_COMMANDS:
            params = _collect_command_map()[command.name].params
            assert DEVICE_FLAG in {
                opt for param in params for opt in getattr(param, "opts", [])
            }


class TestSafetyGuardIsWired:
    """The guard existed but was called from almost nowhere (#4)."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_device_commands_guard_or_are_exempt(self, command: Command) -> None:
        if command.name in GUARD_EXEMPT:
            pytest.skip(f"exempt: {GUARD_EXEMPT[command.name]}")
        assert command.calls_guard, (
            f"{command.name} takes {DEVICE_FLAG} but never calls "
            "_assert_device_safe; add the guard, or record why it is exempt in "
            "GUARD_EXEMPT"
        )

    def test_every_exemption_names_a_real_command(self) -> None:
        # Otherwise an exemption outlives the command it excused and silently
        # covers a future one with the same name.
        assert not set(GUARD_EXEMPT) - {c.name for c in DEVICE_COMMANDS}

    def test_every_exemption_has_a_reason(self) -> None:
        for name, reason in GUARD_EXEMPT.items():
            assert len(reason) > 20, f"{name} needs a real reason, not a placeholder"


class TestDryRunActuallyPreviews:
    """The global flag was parsed and never read (#5)."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_device_commands_preview_or_are_exempt(self, command: Command) -> None:
        if command.name in DRY_RUN_EXEMPT:
            pytest.skip(f"exempt: {DRY_RUN_EXEMPT[command.name]}")
        assert command.previews, (
            f"{command.name} must resolve --dry-run, emit a plan, and return "
            "before doing any work; calling the resolver alone would let "
            "`tfqa --dry-run` execute the write"
        )

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_dry_run_is_offered_as_an_option(self, command: Command) -> None:
        if command.name in DRY_RUN_EXEMPT:
            pytest.skip(f"exempt: {DRY_RUN_EXEMPT[command.name]}")
        assert DRY_RUN_FLAG in command.flags

    def test_every_exemption_names_a_real_command(self) -> None:
        assert not set(DRY_RUN_EXEMPT) - {c.name for c in DEVICE_COMMANDS}


def _pipeline_engine_modules() -> dict[str, object]:
    """Every engine module the pipeline can invoke, taken from its imports."""

    modules: dict[str, object] = {}
    for value in vars(pipeline_mod).values():
        module = inspect.getmodule(value)
        name = getattr(module, "__name__", "")
        if name.startswith("tfqa.tests.") and name not in modules:
            modules[name] = module
    return modules


ENGINE_MODULES = _pipeline_engine_modules()


class TestStatusVocabulary:
    """An unmapped "fail" made counterfeits read as passing stages (#9)."""

    def test_the_engines_were_found(self) -> None:
        # Listing them by hand omitted capacity.quick and workload.smallfiles.
        assert len(ENGINE_MODULES) >= 4

    @pytest.mark.parametrize("name", sorted(ENGINE_MODULES), ids=lambda n: n)
    def test_every_status_an_engine_can_report_normalises(self, name: str) -> None:
        tree = ast.parse(inspect.getsource(ENGINE_MODULES[name]))  # type: ignore[arg-type]
        for status in _status_strings(tree):
            assert status in _VALID_STATUSES or status in _STATUS_SYNONYMS, (
                f"{name} can report status {status!r}, which normalize_status "
                "would map to 'error'"
            )

    def test_synonyms_all_resolve_to_the_vocabulary(self) -> None:
        for raw, mapped in _STATUS_SYNONYMS.items():
            assert mapped in _VALID_STATUSES, f"{raw!r} maps outside the vocabulary"


def _status_strings(tree: ast.AST) -> set[str]:
    """Every string a module could put in a `status` field.

    Covers dict literals, `Literal[...]` annotations on a `status` field, and
    values assigned to a name called `status` -- including the conditional form
    `status = "fail" if ... else "ok"`, which a dict-literal scan misses.
    """

    found: set[str] = set()

    def collect(node: ast.AST) -> None:
        """Gather strings in value position only.

        Walking everything picked up incidental literals -- the key inside
        `status = "fail" if parsed.get("fake_detected") else "ok"` is not a
        status, so a conditional's test and any call arguments are skipped.
        """

        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                found.add(node.value)
            return
        if isinstance(node, ast.IfExp):
            collect(node.body)
            collect(node.orelse)
            return
        if isinstance(node, ast.Call):
            return
        for child in ast.iter_child_nodes(node):
            collect(child)

    for node in ast.walk(tree):
        # {"status": "ok"} and {"status": <expr with literals>}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    collect(value)
        # status: Literal["ok", "fail"]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "status":
                if node.annotation is not None:
                    collect(node.annotation)
                if node.value is not None:
                    collect(node.value)
        # status = "fail" if ... else "ok"
        elif isinstance(node, ast.Assign):
            if any(
                isinstance(t, ast.Name) and t.id.endswith("status")
                for t in node.targets
            ):
                collect(node.value)

    # Names of the fields themselves are not statuses.
    return {value for value in found if value and value != "status"}


class TestDescribeAgreesWithTheCode:
    """`describe` is what an agent reads before running anything."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_declared_tools_are_a_known_shape(self, command: Command) -> None:
        spec = _TOOL_REQUIREMENTS.get(command.name, {})
        assert set(spec) <= {"required_tools", "optional_tools", "degradation"}

    @pytest.mark.parametrize(
        "command", [c for c in DEVICE_COMMANDS if c.calls_guard], ids=lambda c: c.name
    )
    def test_guarded_commands_declare_themselves_destructive(
        self, command: Command
    ) -> None:
        # quick-test, surface-scan, filesystem-check, performance, and pipeline
        # all reported destructive=False while calling the guard -- and
        # quick-test is the one that wrote to a mounted card in practice.
        assert _describe(command.name)["destructive"] is True, (
            f"{command.name} calls the safety guard but describe says "
            "destructive=False; an agent would run it unprepared"
        )

    @pytest.mark.parametrize(
        "command", [c for c in DEVICE_COMMANDS if c.calls_guard], ids=lambda c: c.name
    )
    def test_destructive_commands_say_under_what_conditions(
        self, command: Command
    ) -> None:
        # Several are destructive only with a particular flag, and an agent
        # cannot tell which from a bare boolean.
        assert _describe(command.name)["destructive_when"]

    def test_every_command_carries_the_key(self) -> None:
        assert _describe("detect")["destructive_when"] is None
