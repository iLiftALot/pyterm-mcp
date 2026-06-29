from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, cast

import mcp.types
import pytest
from fastmcp import Client, Context
from iterm2_api_wrapper.typings import (
    CommandExecutionResult,
    CommandExecutionStatus,
    CommandExitCode,
)
from mcp.types import TextContent
from pydantic import ValidationError

from pyterm_mcp import auto_cli, cli
from pyterm_mcp import main as pyterm_main
from pyterm_mcp.types import CommandOperation, CommandResult


_UNSET = object()


class DummyState:
    def __init__(
        self,
        *,
        result: CommandExecutionResult | None = None,
        exc: Exception | None = None,
        escape_exc: Exception | None = None,
    ) -> None:
        self.result = result or execution_result(" command output \n")
        self.exc = exc
        self.escape_exc = escape_exc
        self.calls: list[dict[str, Any]] = []
        self.escape_calls: list[tuple[tuple[str, ...], bool]] = []

    async def run_command(self, **kwargs: Any) -> CommandExecutionResult:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result

    async def send_escape_sequence(self, *sequences: str, broadcast: bool) -> None:
        self.escape_calls.append((sequences, broadcast))
        if self.escape_exc is not None:
            raise self.escape_exc


class DummyClient:
    def __init__(self, state: DummyState) -> None:
        self.state = state

    async def get_state_async(self) -> DummyState:
        return self.state


@dataclass(frozen=True)
class DummyContext:
    @property
    def session_id(self) -> str:
        return "test-session"

    @classmethod
    def asContext(cls) -> Context:
        return cast(Context, cls())


def fake_context() -> Context:
    return DummyContext.asContext()


def command_status(
    *,
    command: str | None = "echo hi",
    exit_code: CommandExitCode | int = CommandExitCode.SUCCESS,
    timed_out: bool = False,
) -> CommandExecutionStatus:
    return CommandExecutionStatus(prompt_id="prompt-1", command=command, exit_code=exit_code, timed_out=timed_out)


def execution_result(
    output: str = "command output",
    status: CommandExecutionStatus | None | object = _UNSET,
) -> CommandExecutionResult:
    resolved_status = command_status() if status is _UNSET else status
    assert resolved_status is None or isinstance(resolved_status, CommandExecutionStatus)
    return CommandExecutionResult(output=output, status=resolved_status)


async def cancel_pending_operations() -> None:
    pending = [
        op.task
        for session in pyterm_main._RUNNING_COMMANDS.values()
        for op in session.operations.values()
        if not op.task.done()
    ]
    for task in pending:
        task.cancel()
    for task in pending:
        with suppress(asyncio.CancelledError):
            await task
    pyterm_main._RUNNING_COMMANDS.clear()
    pyterm_main._COMPLETED_COMMANDS.clear()


@pytest.fixture(autouse=True)
def clear_running_commands() -> Iterator[None]:
    pyterm_main._RUNNING_COMMANDS.clear()
    pyterm_main._COMPLETED_COMMANDS.clear()
    yield
    pyterm_main._RUNNING_COMMANDS.clear()
    pyterm_main._COMPLETED_COMMANDS.clear()


@pytest.mark.parametrize(
    "status",
    [
        "running",
        "success",
        "error",
        "unknown (Shell-Integration Disabled)",
        "timeout",
        "cancelled",
        "not_found",
    ],
)
def test_command_result_accepts_current_status_values(
    status: pyterm_main.CommandStatus,
) -> None:
    result = CommandResult(status=status, command="pwd", broadcast=False, path=None, timeout=1.0, output="ok")

    assert result.model_dump() == {
        "status": status,
        "command": "pwd",
        "broadcast": False,
        "path": None,
        "timeout": 1.0,
        "output": "ok",
    }


def test_command_result_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        CommandResult(
            status="pending",  # type: ignore[arg-type]
            command="pwd",
            broadcast=False,
            path=None,
            timeout=1.0,
            output="ok",
        )


def test_fastmcp_server_exposes_current_source_tool_surface() -> None:
    async def scenario() -> set[str]:
        async with Client(pyterm_main.mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    assert asyncio.run(scenario()) == {
        "send_command",
        "start_command",
        "get_command_status",
        "cancel_command",
        "resend_command",
    }


def test_get_state_uses_session_scoped_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState()
    calls: list[dict[str, Any]] = []

    async def fake_get_shared_client(**kwargs: Any) -> DummyClient:
        calls.append(kwargs)
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._get_state_for_session(fake_context().session_id))

    assert result is state
    assert calls == [{"service_name": "pyterm-mcp", "extra_id": "test-session"}]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, "unknown (Shell-Integration Disabled)"),
        (command_status(exit_code=CommandExitCode.SUCCESS), "success"),
        (command_status(exit_code=CommandExitCode.GENERAL_FAILURE), "error"),
        (
            command_status(exit_code=CommandExitCode.GENERAL_FAILURE, timed_out=True),
            "timeout",
        ),
    ],
)
def test_configure_status_maps_wrapper_execution_status(
    status: CommandExecutionStatus | None, expected: pyterm_main.CommandStatus
) -> None:
    assert pyterm_main._configure_status(status) == expected


def test_send_command_helper_uses_wrapper_output_and_reported_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(result=execution_result(" hello from terminal \n", command_status(command="echo actual")))

    async def fake_get_shared_client(**kwargs: Any) -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(
        pyterm_main._send_command("echo hi", path="/tmp", broadcast=True, timeout=2.5, ctx=fake_context())
    )

    assert result == CommandResult(
        status="success",
        command="echo actual",
        broadcast=True,
        path="/tmp",
        timeout=2.5,
        output=" hello from terminal \n",
    )
    assert state.calls == [{"command": "echo hi", "broadcast": True, "path": "/tmp", "timeout": 2.5}]


def test_send_command_helper_falls_back_to_requested_command_when_status_has_no_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(result=execution_result("ok", command_status(command=None)))

    async def fake_get_shared_client(**kwargs: Any) -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._send_command("echo requested", ctx=fake_context()))

    assert result.status == "success"
    assert result.command == "echo requested"


def test_send_command_helper_reports_unknown_when_shell_integration_status_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(result=CommandExecutionResult(output="", status=None))

    async def fake_get_shared_client(**kwargs: Any) -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._send_command("true", ctx=fake_context()))

    assert result.status == "unknown (Shell-Integration Disabled)"
    assert result.output == ""


def test_send_command_helper_returns_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(exc=RuntimeError("terminal unavailable"))

    async def fake_get_shared_client(**kwargs: Any) -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._send_command("pwd", timeout=0.5, ctx=fake_context()))

    assert result == CommandResult(
        status="error",
        command="pwd",
        broadcast=False,
        path=None,
        timeout=0.5,
        output="terminal unavailable",
    )


def test_interrupt_terminal_sends_ctrl_c_and_swallows_delivery_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_state = DummyState()

    async def get_working_state_for_session(session_id: str) -> DummyState:
        assert session_id == "test-session"
        return working_state

    monkeypatch.setattr(pyterm_main, "_get_state_for_session", get_working_state_for_session)
    asyncio.run(pyterm_main._interrupt_terminal(broadcast=True, ctx=fake_context()))
    assert working_state.escape_calls == [(("CNTRL_C", "CNTRL_C"), True)]
    failing_state = DummyState(escape_exc=RuntimeError("rpc unavailable"))

    async def get_failing_state_for_session(session_id: str) -> DummyState:
        assert session_id == "test-session"
        return failing_state

    monkeypatch.setattr(pyterm_main, "_get_state_for_session", get_failing_state_for_session)
    asyncio.run(pyterm_main._interrupt_terminal(broadcast=False, ctx=fake_context()))


def test_send_command_tool_returns_text_content_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_command(
        command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
    ) -> CommandResult:
        return CommandResult(
            status="success",
            command=command,
            broadcast=broadcast,
            path=path,
            timeout=timeout,
            output="done",
        )

    monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

    result = asyncio.run(pyterm_main.send_command("date", fake_context(), path=None, broadcast=False, timeout=3.0))

    assert result.isError is False
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.content[0].text == "done"

    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "success"
    assert result.structuredContent["command"] == "date"
    assert result.structuredContent["broadcast"] is False
    assert result.structuredContent["path"] is None
    assert result.structuredContent["timeout"] == 3.0
    assert result.structuredContent["output"] == "done"
    assert result.structuredContent["is_done"] is True
    assert isinstance(result.structuredContent["command_id"], str)
    assert result.structuredContent["command_id"]
    assert pyterm_main._RUNNING_COMMANDS == {}
    assert result.structuredContent["command_id"] in pyterm_main._COMPLETED_COMMANDS["test-session"].operations


def test_send_command_tool_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_command(
        command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
    ) -> CommandResult:
        return CommandResult(
            status="error",
            command=command,
            broadcast=broadcast,
            path=path,
            timeout=timeout,
            output="boom",
        )

    monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

    result = asyncio.run(pyterm_main.send_command("date", fake_context(), path="/work", broadcast=True, timeout=3.0))

    assert result.isError is True
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.content[0].text == "boom"
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "error"
    assert result.structuredContent["command"] == "date"
    assert result.structuredContent["broadcast"] is True
    assert result.structuredContent["path"] == "/work"
    assert result.structuredContent["timeout"] == 3.0
    assert result.structuredContent["output"] == "boom"
    assert result.structuredContent["is_done"] is True
    assert isinstance(result.structuredContent["command_id"], str)
    assert result.structuredContent["command_id"]
    assert pyterm_main._RUNNING_COMMANDS == {}
    assert result.structuredContent["command_id"] in pyterm_main._COMPLETED_COMMANDS["test-session"].operations


def test_send_command_tool_returns_running_state_when_response_timeout_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="done",
            )

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

        result = await pyterm_main.send_command(
            "sleep 30",
            fake_context(),
            path="/work",
            broadcast=True,
            timeout=30.0,
            response_timeout=0.0,
        )

        assert result.isError is False
        assert result.structuredContent is not None
        assert result.structuredContent["status"] == "running"
        assert result.structuredContent["command"] == "sleep 30"
        assert result.structuredContent["broadcast"] is True
        assert result.structuredContent["path"] == "/work"
        assert result.structuredContent["timeout"] == 30.0
        assert result.structuredContent["is_done"] is False
        assert isinstance(result.content[0], TextContent) and "Use get_command_status" in result.content[0].text
        assert result.structuredContent["command_id"] in pyterm_main._RUNNING_COMMANDS["test-session"].operations

        await cancel_pending_operations()

    asyncio.run(scenario())


def test_start_and_get_command_status_track_running_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

        state = await pyterm_main.start_command(
            "tail -f log", fake_context(), path="/tmp", broadcast=False, timeout=9.0
        )
        status = await pyterm_main.get_command_status(state.command_id)

        assert state == status
        assert status.status == "running"
        assert status.session_id == "test-session"
        assert status.output == "Command is still running."

        await cancel_pending_operations()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_output"),
    [
        (asyncio.CancelledError(), "cancelled", "Command was cancelled."),
        (TimeoutError("too slow"), "timeout", "too slow"),
        (RuntimeError("boom"), "error", "boom"),
    ],
)
def test_operation_state_maps_terminal_task_failures(
    exc: BaseException, expected_status: pyterm_main.CommandStatus, expected_output: str
) -> None:
    async def scenario() -> None:
        async def fail() -> CommandResult:
            raise exc

        command_id = "op-1:test-session"
        task = asyncio.create_task(fail())
        with suppress(BaseException):
            await task
        pyterm_main._store_operation(
            pyterm_main._RUNNING_COMMANDS,
            CommandOperation(
                command_id=command_id,
                session_id="test-session",
                command="cmd",
                path=None,
                broadcast=False,
                timeout=1.0,
                task=task,
            ),
        )

        state = pyterm_main._operation_state(command_id)

        assert state.status == expected_status
        assert state.output == expected_output
        assert state.is_done is True

    asyncio.run(scenario())


def test_get_command_status_reports_missing_and_malformed_ids() -> None:
    missing = asyncio.run(pyterm_main.get_command_status("missing:test-session"))

    assert missing.status == "not_found"
    assert missing.session_id == "test-session"
    assert missing.output == "No command operation found for id: missing:test-session"

    malformed = asyncio.run(pyterm_main.get_command_status("malformed"))

    assert malformed.status == "not_found"
    assert malformed.session_id == ""
    assert malformed.output == "No command operation found for id: malformed"


@pytest.mark.parametrize("nullish_command_id", [None, "", "none", "null", " NULL "])
def test_cancel_command_treats_nullish_command_id_as_cancel_all(
    monkeypatch: pytest.MonkeyPatch,
    nullish_command_id: str | None,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        async def fake_interrupt_terminal(
            *, broadcast: bool, ctx: Any | None = None, session_id: str | None = None
        ) -> None:
            return None

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)
        monkeypatch.setattr(pyterm_main, "_interrupt_terminal", fake_interrupt_terminal)

        await pyterm_main.start_command("one", fake_context())
        result = await pyterm_main.cancel_command(fake_context(), nullish_command_id)

        assert result.status == "cancelled"
        assert result.output == "1 running commands have been cancelled."
        assert pyterm_main._RUNNING_COMMANDS == {}

    asyncio.run(scenario())


def test_cancel_command_removes_running_operation_and_interrupts_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        interrupt_calls: list[bool] = []

        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        async def fake_interrupt_terminal(
            *, broadcast: bool, ctx: Any | None = None, session_id: str | None = None
        ) -> None:
            interrupt_calls.append(broadcast)

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)
        monkeypatch.setattr(pyterm_main, "_interrupt_terminal", fake_interrupt_terminal)

        state = await pyterm_main.start_command("sleep 30", fake_context(), broadcast=True, timeout=30.0)
        result = await pyterm_main.cancel_command(fake_context(), state.command_id)

        assert result.status == "cancelled"
        assert result.output == "Command was cancelled."
        assert interrupt_calls == [True]
        assert state.command_id not in pyterm_main._iter_command_ids(pyterm_main._RUNNING_COMMANDS)
        assert state.command_id in pyterm_main._COMPLETED_COMMANDS["test-session"].operations

    asyncio.run(scenario())


def test_cancel_command_without_id_cancels_all_running_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        interrupt_calls: list[bool] = []

        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        async def fake_interrupt_terminal(
            *, broadcast: bool, ctx: Any | None = None, session_id: str | None = None
        ) -> None:
            interrupt_calls.append(broadcast)

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)
        monkeypatch.setattr(pyterm_main, "_interrupt_terminal", fake_interrupt_terminal)

        first = await pyterm_main.start_command("one", fake_context(), broadcast=False)
        second = await pyterm_main.start_command("two", fake_context(), broadcast=True)
        result = await pyterm_main.cancel_command(fake_context())

        assert result.status == "cancelled"
        assert result.command_id == f"{first.command_id}, {second.command_id}"
        assert result.output == "2 running commands have been cancelled."
        assert interrupt_calls == [False, True]
        assert pyterm_main._RUNNING_COMMANDS == {}
        assert set(pyterm_main._COMPLETED_COMMANDS["test-session"].operations) == {
            first.command_id,
            second.command_id,
        }

    asyncio.run(scenario())


def test_resend_command_reuses_original_operation_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

        original = await pyterm_main.start_command(
            "npm test", fake_context(), path="/repo", broadcast=True, timeout=12.0
        )
        resent = await pyterm_main.resend_command(fake_context(), original.command_id, cancel_existing=False)

        assert resent.command_id != original.command_id
        assert resent.status == "running"
        assert resent.command == "npm test"
        assert resent.path == "/repo"
        assert resent.broadcast is True
        assert resent.timeout == 12.0
        assert set(pyterm_main._iter_command_ids(pyterm_main._RUNNING_COMMANDS)) == {
            original.command_id,
            resent.command_id,
        }

        await cancel_pending_operations()

    asyncio.run(scenario())


def test_completed_command_status_is_archived_by_session_and_remains_queryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="finished",
            )

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

        started = await pyterm_main.start_command("echo done", fake_context(), path="/repo", timeout=4.0)
        await asyncio.sleep(0)

        first_status = await pyterm_main.get_command_status(started.command_id)
        archived_status = await pyterm_main.get_command_status(started.command_id)

        assert first_status.status == "success"
        assert first_status.output == "finished"
        assert archived_status == first_status
        assert pyterm_main._RUNNING_COMMANDS == {}
        assert started.command_id in pyterm_main._COMPLETED_COMMANDS["test-session"].operations

    asyncio.run(scenario())


def test_resend_command_can_reuse_completed_operation_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="finished",
            )

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)

        started = await pyterm_main.start_command(
            "npm test", fake_context(), path="/repo", broadcast=True, timeout=12.0
        )
        await asyncio.sleep(0)
        completed = await pyterm_main.get_command_status(started.command_id)
        resent = await pyterm_main.resend_command(fake_context(), completed.command_id)

        assert completed.status == "success"
        assert resent.command_id != completed.command_id
        assert resent.command == "npm test"
        assert resent.path == "/repo"
        assert resent.broadcast is True
        assert resent.timeout == 12.0
        assert completed.command_id in pyterm_main._COMPLETED_COMMANDS["test-session"].operations
        assert resent.command_id in pyterm_main._RUNNING_COMMANDS["test-session"].operations

    asyncio.run(scenario())


def test_resend_command_can_cancel_existing_operation_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        interrupt_calls: list[bool] = []

        async def fake_send_command(
            command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Any
        ) -> CommandResult:
            await asyncio.Event().wait()
            return CommandResult(
                status="success",
                command=command,
                broadcast=broadcast,
                path=path,
                timeout=timeout,
                output="never",
            )

        async def fake_interrupt_terminal(
            *, broadcast: bool, ctx: Any | None = None, session_id: str | None = None
        ) -> None:
            interrupt_calls.append(broadcast)

        monkeypatch.setattr(pyterm_main, "_send_command", fake_send_command)
        monkeypatch.setattr(pyterm_main, "_interrupt_terminal", fake_interrupt_terminal)

        original = await pyterm_main.start_command("python server.py", fake_context(), broadcast=True)
        resent = await pyterm_main.resend_command(fake_context(), original.command_id)

        assert original.command_id not in pyterm_main._iter_command_ids(pyterm_main._RUNNING_COMMANDS)
        assert original.command_id in pyterm_main._COMPLETED_COMMANDS["test-session"].operations
        assert resent.command_id in pyterm_main._RUNNING_COMMANDS["test-session"].operations
        assert resent.command == "python server.py"
        assert interrupt_calls == [True]

        await cancel_pending_operations()

    asyncio.run(scenario())


def test_resend_command_reports_missing_operation() -> None:
    result = asyncio.run(pyterm_main.resend_command(fake_context(), "missing:test-session"))

    assert result.status == "not_found"
    assert result.session_id == "test-session"


def test_cli_inspect_runs_fastmcp_dev_with_project_relative_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_cmd(arg_string: str, **kwargs: Any) -> None:
        calls.append({"arg_string": arg_string, **kwargs})

    monkeypatch.setattr(cli, "_run_cmd", fake_run_cmd)
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    cli.inspect()

    assert calls == [
        {
            "arg_string": "lsof -ti :6274 | xargs kill -9",
            "shell": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        },
        {
            "arg_string": "lsof -ti :6277 | xargs kill -9",
            "shell": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        },
        {"arg_string": "fastmcp dev inspector src/pyterm_mcp/main.py"},
    ]


def test_cli_generate_cli_builds_fastmcp_generate_cli_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "_run_cmd", calls.append)

    cli.generate_cli()
    cli.generate_cli(spec="server.py", output="generated.py", force=True)

    assert calls == [
        "fastmcp generate-cli --server-spec src/pyterm_mcp/main.py --output src/pyterm_mcp/auto_cli.py",
        "fastmcp generate-cli -f --server-spec server.py --output generated.py",
    ]


def test_run_cmd_prints_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def fake_run(command: list[str] | str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(2, command, output="stdout text", stderr="stderr text")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.console, "print", lambda value: printed.append(str(value)))
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    cli._run_cmd("mcp dev src/pyterm_mcp/main.py")

    assert printed == [
        "[red]Error:[/red] Command '['mcp', 'dev', 'src/pyterm_mcp/main.py']' returned non-zero exit status 2."
    ]


def test_run_cmd_prints_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def fake_run(command: list[str] | str, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.console, "print", lambda value: printed.append(str(value)))

    cli._run_cmd("fastmcp dev inspector src/pyterm_mcp/main.py")

    assert printed == ["\n[yellow]MCP inspector stopped.[/yellow]"]


def test_generated_cli_prints_structured_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed_json: list[str] = []
    monkeypatch.setattr(auto_cli.console, "print_json", printed_json.append)

    auto_cli._print_tool_result(SimpleNamespace(is_error=False, structured_content={"status": "success"}, content=[]))

    assert json.loads(printed_json[0]) == {"status": "success"}


def test_generated_cli_prints_error_and_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []
    monkeypatch.setattr(auto_cli.console, "print", printed.append)

    with pytest.raises(SystemExit) as exc:
        auto_cli._print_tool_result(
            SimpleNamespace(
                is_error=True,
                structured_content=None,
                content=[mcp.types.TextContent(type="text", text="boom")],
            )
        )

    assert exc.value.code == 1
    assert printed == ["[bold red]Error:[/bold red] boom"]


def test_generated_cli_call_tool_filters_empty_optional_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    printed: list[Any] = []

    class FakeClient:
        def __init__(self, spec: object) -> None:
            self.spec = spec

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def call_tool(self, tool_name: str, arguments: dict[str, Any], *, raise_on_error: bool) -> Any:
            calls.append(
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "raise_on_error": raise_on_error,
                    "spec": self.spec,
                }
            )
            return SimpleNamespace(is_error=False, structured_content=None, content=[])

    monkeypatch.setattr(auto_cli, "Client", FakeClient)
    monkeypatch.setattr(auto_cli, "_print_tool_result", printed.append)

    asyncio.run(auto_cli._call_tool("send_command", {"keep": 1, "none": None, "empty": [], "items": ["value"]}))

    assert calls == [
        {
            "tool_name": "send_command",
            "arguments": {"keep": 1, "items": ["value"]},
            "raise_on_error": False,
            "spec": auto_cli.CLIENT_SPEC,
        }
    ]
    assert len(printed) == 1


def test_generated_cli_tool_wrappers_parse_json_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call_tool(tool_name: str, arguments: dict[str, Any]) -> None:
        calls.append((tool_name, arguments))

    monkeypatch.setattr(auto_cli, "_call_tool", fake_call_tool)

    asyncio.run(
        auto_cli.send_command(
            command="pwd",
            path='"/tmp/project"',
            broadcast=True,
            timeout=2.5,
            response_timeout="0.5",
        )
    )
    asyncio.run(auto_cli.start_command(command="npm test", path='"/tmp/project"', broadcast=False, timeout=3.0))
    asyncio.run(auto_cli.cancel_command(command_id="null"))
    asyncio.run(auto_cli.resend_command(command_id="abc:test-session", cancel_existing=False))

    assert calls == [
        (
            "send_command",
            {
                "command": "pwd",
                "path": "/tmp/project",
                "broadcast": True,
                "timeout": 2.5,
                "response_timeout": 0.5,
            },
        ),
        (
            "start_command",
            {
                "command": "npm test",
                "path": "/tmp/project",
                "broadcast": False,
                "timeout": 3.0,
            },
        ),
        ("cancel_command", {"command_id": None}),
        ("resend_command", {"command_id": "abc:test-session", "cancel_existing": False}),
    ]
