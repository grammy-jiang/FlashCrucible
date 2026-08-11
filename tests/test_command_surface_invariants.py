"""Structural invariants over the CLI command surface.

Two AI reviewers raised 24 findings across #4-#9. None were prevented by
documentation -- the repository already carried an instruction file describing
guards that were wired into nothing. These tests enforce four of those rules
mechanically instead, so a new command cannot quietly skip them:

1. anything taking `--device` clears the safety guard, or is exempt for a
   recorded reason;
2. anything taking `--device` supports `--dry-run`, on the same terms;
3. no engine reports a status the pipeline vocabulary would not recognise;
4. `describe` agrees with the code about which commands are destructive.

The command list is derived from the Typer tree, not maintained here, so
adding a command adds it to these checks automatically. Only the *exemptions*
are written down, and each one carries its reason.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import NamedTuple

import pytest

from typer.testing import CliRunner

from tfqa.cli.main import _TOOL_REQUIREMENTS, _collect_command_map, app
from tfqa.core.models import CLIResponse
from tfqa.orchestration.pipeline import _STATUS_SYNONYMS, _VALID_STATUSES
from tfqa.tests.capacity import full as capacity_full
from tfqa.tests.performance import basic as perf_basic
from tfqa.tests.performance import random as perf_random
from tfqa.tests.surface import scan as surface_scan

CLI_SOURCE = pathlib.Path(inspect.getfile(app.registered_commands[0].callback))  # type: ignore[arg-type]

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
    takes_device: bool
    calls_guard: bool
    calls_dry_run: bool


def _commands() -> list[Command]:
    """Read the command surface out of the CLI module's AST."""

    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    found: list[Command] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        name = None
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "command"
            ):
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    name = keyword.value.value
        if name is None:
            continue
        params = {arg.arg for arg in node.args.args}
        called = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        found.append(
            Command(
                name=name,
                takes_device="device" in params,
                calls_guard="_assert_device_safe" in called,
                calls_dry_run="_resolve_dry_run" in called,
            )
        )
    return found


DEVICE_COMMANDS = [c for c in _commands() if c.takes_device]

_runner = CliRunner()


def _describe(command: str) -> dict[str, object]:
    result = _runner.invoke(app, ["describe", command, "--output", "json"])
    assert result.exit_code == 0, result.stdout
    return dict(CLIResponse.model_validate_json(result.stdout).data["describe"])


def test_the_command_surface_was_actually_found() -> None:
    # A parsing change that silently found nothing would make every test below
    # vacuously pass.
    assert len(DEVICE_COMMANDS) >= 10


class TestSafetyGuardIsWired:
    """The guard existed but was called from almost nowhere (#4)."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_device_commands_guard_or_are_exempt(self, command: Command) -> None:
        if command.name in GUARD_EXEMPT:
            pytest.skip(f"exempt: {GUARD_EXEMPT[command.name]}")
        assert command.calls_guard, (
            f"{command.name} takes --device but never calls _assert_device_safe; "
            "add the guard, or record why it is exempt in GUARD_EXEMPT"
        )

    def test_every_exemption_names_a_real_command(self) -> None:
        # Otherwise an exemption outlives the command it excused and silently
        # covers a future one with the same name.
        names = {c.name for c in DEVICE_COMMANDS}
        assert not set(GUARD_EXEMPT) - names

    def test_every_exemption_has_a_reason(self) -> None:
        for name, reason in GUARD_EXEMPT.items():
            assert len(reason) > 20, f"{name} needs a real reason, not a placeholder"


class TestDryRunIsSupported:
    """The global flag was parsed and never read (#5)."""

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_device_commands_support_dry_run_or_are_exempt(
        self, command: Command
    ) -> None:
        if command.name in DRY_RUN_EXEMPT:
            pytest.skip(f"exempt: {DRY_RUN_EXEMPT[command.name]}")
        assert command.calls_dry_run, (
            f"{command.name} takes --device but never calls _resolve_dry_run; "
            "wire it up, or record why it is exempt in DRY_RUN_EXEMPT"
        )

    @pytest.mark.parametrize("command", DEVICE_COMMANDS, ids=lambda c: c.name)
    def test_dry_run_is_offered_as_an_option(self, command: Command) -> None:
        if command.name in DRY_RUN_EXEMPT:
            pytest.skip(f"exempt: {DRY_RUN_EXEMPT[command.name]}")
        params = _collect_command_map()[command.name].params
        flags = {opt for param in params for opt in getattr(param, "opts", [])}
        assert "--dry-run" in flags, f"{command.name} has no --dry-run option"


class TestStatusVocabulary:
    """An unmapped "fail" made counterfeits read as passing stages (#9)."""

    ENGINE_MODULES = (perf_basic, perf_random, surface_scan, capacity_full)

    @pytest.mark.parametrize("module", ENGINE_MODULES, ids=lambda m: m.__name__)
    def test_engine_statuses_survive_normalisation(self, module: object) -> None:
        tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
        reported = {
            value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant)
            and key.value == "status"
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
        }
        for status in reported:
            assert status in _VALID_STATUSES or status in _STATUS_SYNONYMS, (
                f"{module.__name__} reports status {status!r}, which "  # type: ignore[attr-defined]
                "normalize_status would map to 'error'"
            )

    def test_synonyms_all_resolve_to_the_vocabulary(self) -> None:
        for raw, mapped in _STATUS_SYNONYMS.items():
            assert mapped in _VALID_STATUSES, f"{raw!r} maps outside the vocabulary"


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
        # An agent reads `describe` before deciding whether something is safe.
        # quick-test, surface-scan, filesystem-check, performance, and pipeline
        # all reported destructive=False while calling the guard -- and
        # quick-test is the one that wrote to a mounted card in practice.
        described = _describe(command.name)
        assert described["destructive"] is True, (
            f"{command.name} calls the safety guard but describe says "
            "destructive=False; an agent would run it unprepared"
        )

    @pytest.mark.parametrize(
        "command", [c for c in DEVICE_COMMANDS if c.calls_guard], ids=lambda c: c.name
    )
    def test_destructive_commands_say_under_what_conditions(
        self, command: Command
    ) -> None:
        # Several are only destructive with a particular flag, and an agent
        # cannot tell which from a bare boolean.
        assert _describe(command.name)["destructive_when"], (
            f"{command.name} is destructive but does not say when"
        )

    def test_every_command_carries_the_key(self) -> None:
        assert _describe("detect")["destructive_when"] is None
