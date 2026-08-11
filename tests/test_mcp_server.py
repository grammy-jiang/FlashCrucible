"""The MCP server projects the CLI without becoming a second implementation.

Two properties matter more than the protocol details. The tool descriptions
must be *derived*, because a hand-written copy drifts and an agent acts on what
it is told. And a destructive tool reached over MCP must be exactly as hard to
fire as the same command typed into a shell -- an interface that is easier to
say yes to is the one an agent will find.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import pytest
from jsonschema import Draft7Validator

from tfqa.cli.main import _build_describe_registry, _collect_command_map
from tfqa.mcp import tools as tool_defs
from tfqa.mcp.server import PROTOCOL_VERSION, Server, serve

REGISTRY = _build_describe_registry()
TOOLS = {tool["name"]: tool for tool in tool_defs.build_tools(REGISTRY)}


def _request(method: str, params: dict[str, Any] | None = None, identifier: Any = 1):
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def _respond(server: Server, request: dict[str, Any]) -> dict[str, Any]:
    """A request always draws a response; only notifications do not."""

    response = server.handle(request)
    assert response is not None
    return response


@pytest.fixture(scope="module")
def server() -> Server:
    return Server(REGISTRY)


class TestToolsAreDerived:
    def test_every_command_is_a_tool(self) -> None:
        # A command an agent cannot reach over MCP is a reason to drop back to
        # parsing stdout, which is what this interface exists to end.
        expected = {
            tool_defs.tool_name(name)
            for name in _collect_command_map()
            if name not in tool_defs.NOT_TOOLS
        }
        assert set(TOOLS) == expected

    def test_the_excluded_commands_are_real(self) -> None:
        assert not tool_defs.NOT_TOOLS - set(_collect_command_map())

    def test_options_become_parameters(self) -> None:
        # Derived, not restated: adding a CLI option must add a parameter with
        # no edit to the MCP layer.
        declared = {
            option["name"]
            for option in REGISTRY["quick-test"]["options"]
            if option["name"] not in tool_defs.CONTROLLED_OPTIONS
        }
        assert declared <= set(TOOLS["quick-test"]["inputSchema"]["properties"])

    def test_the_output_schema_is_the_shipped_result_schema(self) -> None:
        assert TOOLS["quick-test"]["outputSchema"]["$id"].endswith(
            "quick-test.result.schema.json"
        )

    def test_every_tool_declares_an_output_schema(self) -> None:
        # Without one the results are untyped, which is the gap this was
        # deliberately built after #16 to avoid.
        assert all(tool.get("outputSchema") for tool in TOOLS.values())

    def test_required_arguments_are_marked_required(self) -> None:
        assert TOOLS["quick-test"]["inputSchema"]["required"] == ["device"]

    def test_unknown_parameters_are_rejected_by_the_schema(self) -> None:
        assert TOOLS["quick-test"]["inputSchema"]["additionalProperties"] is False

    def test_choices_become_an_enum(self) -> None:
        # `describe` records allowed values; restating them here would let the
        # two disagree.
        for tool in TOOLS.values():
            for name, schema in tool["inputSchema"]["properties"].items():
                descriptor = next(
                    (
                        option
                        for option in REGISTRY[tool["annotations"]["title"]]["options"]
                        if option["name"] == name
                    ),
                    None,
                )
                if descriptor and descriptor.get("allowed_values"):
                    assert schema["enum"] == list(descriptor["allowed_values"])

    def test_an_unknown_click_type_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(tool_defs.ToolError):
            tool_defs.input_schema(
                {
                    "name": "x",
                    "arguments": [{"name": "a", "type": "wat", "required": True}],
                    "options": [],
                }
            )


class TestDestructiveToolsAreNotEasier:
    @pytest.mark.parametrize(
        "command",
        sorted(
            name
            for name, metadata in REGISTRY.items()
            if metadata.get("destructive") and name not in tool_defs.NOT_TOOLS
        ),
    )
    def test_a_destructive_tool_says_so(self, command: str) -> None:
        tool = TOOLS[tool_defs.tool_name(command)]
        assert tool["annotations"]["destructiveHint"] is True
        assert tool["annotations"]["readOnlyHint"] is False
        assert "DESTRUCTIVE" in tool["description"]

    @pytest.mark.parametrize(
        "command",
        sorted(
            name
            for name, metadata in REGISTRY.items()
            if metadata.get("destructive") and name not in tool_defs.NOT_TOOLS
        ),
    )
    def test_confirmation_is_reachable(self, command: str) -> None:
        # Most destructive commands take `--force` locally but read
        # confirmation from the global `--yes`. Without a `yes` parameter an
        # agent could be refused with no documented way to comply, and the
        # obvious next move is to work around the guard.
        assert "yes" in TOOLS[tool_defs.tool_name(command)]["inputSchema"]["properties"]

    def test_neither_flag_is_supplied_on_the_callers_behalf(self) -> None:
        argv = tool_defs.argv_for(
            REGISTRY["full-capacity-test"], {"device": "/dev/sdz"}
        )
        assert "--force" not in argv
        assert "--yes" not in argv
        assert "-y" not in argv

    def test_a_false_flag_is_an_absent_flag(self) -> None:
        # `--force false` would arm the run: click sees the flag, not the word.
        argv = tool_defs.argv_for(
            REGISTRY["full-capacity-test"], {"device": "/dev/sdz", "force": False}
        )
        assert "--force" not in argv

    def test_both_flags_reach_the_command_line_when_asked_for(self) -> None:
        argv = tool_defs.argv_for(
            REGISTRY["full-capacity-test"],
            {"device": "/dev/sdz", "force": True, "yes": True},
        )
        assert "--force" in argv and "--yes" in argv

    def test_the_global_yes_precedes_the_command(self) -> None:
        # quick-test has no local --yes, so confirmation is the global flag and
        # click only accepts it before the command name.
        argv = tool_defs.argv_for(
            REGISTRY["quick-test"], {"device": "/dev/sdz", "yes": True}
        )
        assert argv.index("--yes") < argv.index("quick-test")

    def test_the_guard_still_refuses_over_mcp(self, server: Server) -> None:
        # The point of running the real CLI: this refusal is not reimplemented
        # here, it is the same code path a shell user hits.
        result = _respond(
            server,
            _request(
                "tools/call",
                {
                    "name": "full-capacity-test",
                    "arguments": {"device": "/dev/sdz", "force": True},
                },
            ),
        )
        payload = json.loads(result["result"]["content"][0]["text"])
        assert payload["status"] == "error"
        assert result["result"]["isError"] is True


class TestArgumentRendering:
    def test_output_is_forced_to_json(self) -> None:
        argv = tool_defs.argv_for(REGISTRY["detect"], {})
        assert argv[:2] == ["--output", "json"]

    def test_the_caller_cannot_choose_human_output(self) -> None:
        with pytest.raises(tool_defs.ToolError):
            tool_defs.argv_for(REGISTRY["detect"], {"output": "human"})

    def test_unknown_arguments_are_refused(self) -> None:
        # Dropping a misspelled `force` would silently give a different run
        # than the one asked for.
        with pytest.raises(tool_defs.ToolError) as excinfo:
            tool_defs.argv_for(REGISTRY["quick-test"], {"devise": "/dev/sdz"})
        assert "devise" in str(excinfo.value)

    def test_types_are_checked_before_the_subprocess(self) -> None:
        with pytest.raises(tool_defs.ToolError):
            tool_defs.argv_for(
                REGISTRY["full-capacity-test"],
                {"device": "/dev/sdz", "block_size": "big"},
            )

    def test_a_boolean_is_not_an_integer(self) -> None:
        with pytest.raises(tool_defs.ToolError):
            tool_defs.argv_for(
                REGISTRY["full-capacity-test"],
                {"device": "/dev/sdz", "block_size": True},
            )

    def test_subcommands_keep_their_segments(self) -> None:
        argv = tool_defs.argv_for(REGISTRY["config show"], {})
        assert argv[-2:] == ["config", "show"]

    def test_a_positional_argument_is_rendered_without_a_flag(self) -> None:
        argv = tool_defs.argv_for(REGISTRY["describe"], {"command": "quick-test"})
        assert argv[-2:] == ["describe", "quick-test"]

    def test_long_flags_are_preferred(self) -> None:
        # Short flags are the ones that get renamed.
        argv = tool_defs.argv_for(REGISTRY["quick-test"], {"device": "/dev/sdz"})
        assert "--device" in argv and "-d" not in argv


class TestProtocol:
    def test_initialize_reports_the_version_it_implements(self, server: Server) -> None:
        result = _respond(
            server, _request("initialize", {"protocolVersion": "1999-01-01"})
        )
        assert result["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert result["result"]["serverInfo"]["name"] == "flashcrucible"

    def test_initialize_advertises_tools(self, server: Server) -> None:
        result = _respond(server, _request("initialize", {}))
        assert "tools" in result["result"]["capabilities"]

    def test_tools_list_returns_every_tool(self, server: Server) -> None:
        result = _respond(server, _request("tools/list"))
        assert len(result["result"]["tools"]) == len(TOOLS)

    def test_a_notification_draws_no_response(self, server: Server) -> None:
        # Replying to a message with no id gives the client a response it is
        # not tracking.
        assert (
            server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            is None
        )

    def test_an_unknown_method_is_a_protocol_error(self, server: Server) -> None:
        result = _respond(server, _request("tools/destroy"))
        assert result["error"]["code"] == -32601

    def test_a_non_jsonrpc_message_is_refused(self, server: Server) -> None:
        assert _respond(server, {"id": 1, "method": "ping"})["error"]["code"] == -32600

    def test_ping_is_answered(self, server: Server) -> None:
        assert _respond(server, _request("ping"))["result"] == {}

    def test_malformed_json_is_a_parse_error(self, server: Server) -> None:
        out = io.StringIO()
        serve(io.StringIO("{not json\n"), out, server)
        assert json.loads(out.getvalue())["error"]["code"] == -32700

    def test_the_loop_answers_each_line(self, server: Server) -> None:
        lines = "\n".join(
            json.dumps(_request(method, identifier=index))
            for index, method in enumerate(["ping", "tools/list"])
        )
        out = io.StringIO()
        serve(io.StringIO(lines + "\n"), out, server)
        assert [json.loads(line)["id"] for line in out.getvalue().splitlines()] == [
            0,
            1,
        ]

    def test_blank_lines_are_ignored(self, server: Server) -> None:
        out = io.StringIO()
        serve(io.StringIO("\n\n" + json.dumps(_request("ping")) + "\n"), out, server)
        assert len(out.getvalue().splitlines()) == 1


class TestCalling:
    def test_a_real_call_returns_a_validating_envelope(self, server: Server) -> None:
        result = _respond(
            server, _request("tools/call", {"name": "capabilities", "arguments": {}})
        )["result"]

        payload = result["structuredContent"]
        assert payload["status"] == "ok"
        assert result["isError"] is False
        # The advertised outputSchema must describe what the tool really
        # returns, or it is worse than none.
        Draft7Validator(TOOLS["capabilities"]["outputSchema"]).validate(payload)

    def test_a_failed_test_is_not_a_transport_error(self, server: Server) -> None:
        # A counterfeit card detected is the tool working. Marking it isError
        # invites an agent to retry rather than report.
        completed = type(
            "R",
            (),
            {
                "stdout": json.dumps(
                    {
                        "status": "fail",
                        "command": "quick-test",
                        "message": "",
                        "data": {},
                    }
                ),
                "stderr": "",
                "returncode": 1,
            },
        )()
        with patch.object(Server, "_run", return_value=completed):
            result = _respond(
                server,
                _request(
                    "tools/call",
                    {"name": "quick-test", "arguments": {"device": "/dev/sdz"}},
                ),
            )["result"]
        assert result["isError"] is False

    def test_an_unknown_tool_is_reported_to_the_agent(self, server: Server) -> None:
        result = _respond(server, _request("tools/call", {"name": "rm-rf"}))["result"]
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]

    def test_bad_arguments_are_reported_to_the_agent(self, server: Server) -> None:
        # An argument error is the agent's to fix, so it belongs in the result
        # rather than in a transport-level error it cannot see.
        result = _respond(
            server,
            _request("tools/call", {"name": "quick-test", "arguments": {"nope": 1}}),
        )["result"]
        assert result["isError"] is True

    def test_output_that_is_not_an_envelope_is_reported(self, server: Server) -> None:
        completed = type(
            "R", (), {"stdout": "segfault", "stderr": "boom", "returncode": 139}
        )()
        with patch.object(Server, "_run", return_value=completed):
            result = _respond(
                server, _request("tools/call", {"name": "detect", "arguments": {}})
            )["result"]
        assert result["isError"] is True
        assert "boom" in result["content"][0]["text"]

    def test_a_run_that_never_returns_is_reported_not_hung(
        self, server: Server
    ) -> None:
        # The server is single-threaded; a blocked call would wedge every other
        # tool, so it is bounded and the caller is told to detach instead.
        with patch.object(Server, "_run", return_value=None):
            result = _respond(
                server,
                _request(
                    "tools/call",
                    {"name": "full-capacity-test", "arguments": {"device": "/dev/sdz"}},
                ),
            )["result"]
        assert result["isError"] is True
        assert "detach" in result["content"][0]["text"]

    def test_the_child_does_not_inherit_the_servers_run_id(
        self, server: Server
    ) -> None:
        # The run id names a state file; inheriting it would make every tool
        # call overwrite the same run.
        with (
            patch.dict("os.environ", {"TFQA_RUN_ID": "server-run"}),
            patch("subprocess.run") as run,
        ):
            run.return_value = type(
                "R", (), {"stdout": "{}", "stderr": "", "returncode": 0}
            )()
            _respond(
                server, _request("tools/call", {"name": "detect", "arguments": {}})
            )

        assert "TFQA_RUN_ID" not in run.call_args.kwargs["env"]
        assert run.call_args.kwargs["env"]["TFQA_MODE"] == "ai"
