from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Iterator, Literal

import pytest
from pydantic import ValidationError

from pyterm_mcp import cli
from pyterm_mcp import main as pyterm_main
from pyterm_mcp.types import CommandResult


class DummyState:
    def __init__(
        self, *, output: str | None = " command output \n", exc: Exception | None = None
    ) -> None:
        self.output = output
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    async def run_command(self, **kwargs: Any) -> str | None:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.output


class DummyClient:
    def __init__(self, state: DummyState) -> None:
        self.state = state

    async def get_state_async(self) -> DummyState:
        return self.state


class DummyContext:
    session_id = "test-session"


def fake_context() -> Any:
    return DummyContext()


@pytest.fixture(autouse=True)
def clear_running_commands() -> Iterator[None]:
    pyterm_main._RUNNING_COMMANDS.clear()
    yield
    pyterm_main._RUNNING_COMMANDS.clear()


@pytest.mark.parametrize("status", ["success", "error"])
def test_command_result_accepts_current_status_values(
    status: Literal["success", "error"],
) -> None:
    result = CommandResult(
        status=status, command="pwd", broadcast=False, path=None, timeout=1.0, output="ok"
    )

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
            status="pending",  # type:ignore
            command="pwd",
            broadcast=False,
            path=None,
            timeout=1.0,
            output="ok",
        )


def test_send_command_helper_returns_stripped_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(output=" hello from terminal \n")

    async def fake_get_shared_client() -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(
        pyterm_main._send_command("echo hi", path="/tmp", broadcast=True, timeout=2.5, ctx=fake_context())
    )

    assert result == CommandResult(
        status="success",
        command="echo hi",
        broadcast=True,
        path="/tmp",
        timeout=2.5,
        output="hello from terminal",
    )
    assert state.calls == [
        {"command": "echo hi", "broadcast": True, "path": "/tmp", "timeout": 2.5}
    ]


def test_send_command_helper_uses_no_output_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(output="")

    async def fake_get_shared_client() -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._send_command("true", ctx=fake_context()))

    assert result.status == "success"
    assert result.output == "<no output>"


def test_send_command_helper_returns_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(exc=RuntimeError("terminal unavailable"))

    async def fake_get_shared_client() -> DummyClient:
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


def test_send_command_tool_returns_text_content_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_command(
        command: str, path: str | None, broadcast: bool, timeout: float
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

    result = asyncio.run(
        pyterm_main.send_command(
            "date", fake_context(), path=None, broadcast=False, timeout=3.0
        )
    )

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


def test_send_command_tool_returns_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_command(
        command: str, path: str | None, broadcast: bool, timeout: float
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

    result = asyncio.run(
        pyterm_main.send_command(
            "date", fake_context(), path="/work", broadcast=True, timeout=3.0
        )
    )

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


def test_run_cmd_prints_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def fake_run(
        command: list[str] | str, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            2, command, output="stdout text", stderr="stderr text"
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.console, "print", lambda value: printed.append(str(value)))
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    cli._run_cmd("mcp dev src/pyterm_mcp/main.py")

    assert printed == [
        "[red]Error:[/red] Command '['mcp', 'dev', 'src/pyterm_mcp/main.py']' returned non-zero exit status 2."
    ]
