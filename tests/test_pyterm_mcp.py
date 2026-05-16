from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pyterm_mcp import cli
from pyterm_mcp import main as pyterm_main
from pyterm_mcp.return_types import CommandResult


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


@pytest.mark.parametrize("status", ["success", "error"])
def test_command_result_accepts_current_status_values(status: str) -> None:
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
            status="pending",
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
        pyterm_main._send_command("echo hi", path="/tmp", broadcast=True, timeout=2.5)
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

    result = asyncio.run(pyterm_main._send_command("true"))

    assert result.status == "success"
    assert result.output == "<no output>"


def test_send_command_helper_returns_error_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DummyState(exc=RuntimeError("terminal unavailable"))

    async def fake_get_shared_client() -> DummyClient:
        return DummyClient(state)

    monkeypatch.setattr(pyterm_main, "get_shared_client", fake_get_shared_client)

    result = asyncio.run(pyterm_main._send_command("pwd", timeout=0.5))

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
        pyterm_main.send_command("date", path=None, broadcast=False, timeout=3.0)
    )

    assert result.isError is False
    assert result.structuredContent is None
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.content[0].text == "done"


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
        pyterm_main.send_command("date", path="/work", broadcast=True, timeout=3.0)
    )

    assert result.isError is True
    assert result.content == []
    assert result.structuredContent == {
        "status": "error",
        "command": "date",
        "broadcast": True,
        "path": "/work",
        "timeout": 3.0,
        "output": "boom",
    }


def test_cli_main_runs_mcp_dev_with_project_relative_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    printed: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="server ready\n", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.console, "print", lambda value: printed.append(str(value)))
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    cli.main()

    assert calls == [
        {
            "command": ["mcp", "dev", "src/pyterm_mcp/main.py"],
            "capture_output": True,
            "text": True,
            "check": True,
            "cwd": str(Path(__file__).resolve().parents[1]),
        }
    ]
    assert printed == ["server ready\n"]


def test_cli_main_prints_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            2, command, output="stdout text", stderr="stderr text"
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    monkeypatch.setattr(cli.console, "print", lambda value: printed.append(str(value)))
    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    cli.main()

    assert printed == ["[red]Error:[/red] stderr text\nstdout text"]
