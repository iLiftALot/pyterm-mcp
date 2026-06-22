from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import overload
from uuid import uuid4

from fastmcp import Context, FastMCP
from iterm2.rpc import RPCException
from iterm2_api_wrapper import CommandExecutionStatus, get_shared_client, iTermState
from iterm2_api_wrapper._logging import PrettyLog
from mcp.types import CallToolResult, TextContent

from pyterm_mcp.types import (
    CommandOperation,
    CommandResult,
    CommandSession,
    CommandState,
    CommandStatus,
    SupportsSessionId,
)


mcp = FastMCP(
    name="PyTerm-MCP",
    instructions="A tool to interact with the user's terminal. Use this tool to run commands in the user's environment and get the output.",
)
_RUNNING_COMMANDS: dict[str, CommandSession] = {}
_COMPLETED_COMMANDS: dict[str, CommandSession] = {}


async def _get_state_for_session(session_id: str) -> iTermState:
    client = await get_shared_client(service_name="pyterm-mcp", extra_id=session_id)
    state = await client.get_state_async()
    return state


def _parse_session_id(command_id: str) -> str:
    return command_id.split(":", 1)[1]


def _build_command_id(command_id: str, session_id: str) -> str:
    return f"{command_id}:{session_id}"


def _command_session(store: dict[str, CommandSession], session_id: str) -> CommandSession:
    return store.setdefault(session_id, CommandSession(session_id=session_id))


def _store_operation(store: dict[str, CommandSession], op: CommandOperation) -> None:
    _command_session(store, op.session_id).operations[op.command_id] = op


def _get_operation(store: dict[str, CommandSession], command_id: str) -> CommandOperation | None:
    session = store.get(_parse_session_id(command_id))
    if session is None:
        return None
    return session.operations.get(command_id)


def _pop_operation(store: dict[str, CommandSession], command_id: str) -> CommandOperation | None:
    session_id = _parse_session_id(command_id)
    session = store.get(session_id)
    if session is None:
        return None

    op = session.operations.pop(command_id, None)
    if not session.operations:
        store.pop(session_id, None)
    return op


def _iter_command_ids(store: dict[str, CommandSession]) -> list[str]:
    return [command_id for session in store.values() for command_id in session.operations]


def _archive_completed_operation(command_id: str) -> CommandOperation | None:
    op = _get_operation(_RUNNING_COMMANDS, command_id)
    if op is None or not op.task.done():
        return None

    op = _pop_operation(_RUNNING_COMMANDS, command_id)
    if op is None:
        return None

    _store_operation(_COMPLETED_COMMANDS, op)
    return op


def _configure_status(status: CommandExecutionStatus | None) -> CommandStatus:
    if status is None:
        return "unknown (Shell-Integration Disabled)"
    if status.timed_out is True:
        return "timeout"
    return "success" if status.succeeded else "error"


@overload
async def _interrupt_terminal(*, broadcast: bool, ctx: SupportsSessionId) -> None: ...
@overload
async def _interrupt_terminal(*, broadcast: bool, session_id: str) -> None: ...
@overload
async def _interrupt_terminal(*, broadcast: bool, ctx: SupportsSessionId, session_id: str) -> None: ...
async def _interrupt_terminal(
    *, broadcast: bool, ctx: SupportsSessionId | None = None, session_id: str | None = None
) -> None:
    """Send Ctrl-C to the active terminal. Best-effort: never fail control tools."""
    try:
        if ctx is None and session_id is None:
            raise ValueError("Either ctx or session_id must be provided.")

        resolved_session_id = session_id if ctx is None else ctx.session_id
        assert resolved_session_id
        state = await _get_state_for_session(resolved_session_id)
        await state.send_escape_sequence("CNTRL_C", "CNTRL_C", broadcast=broadcast)
    except Exception as e:
        if isinstance(e, RPCException):
            # TODO: Maybe do something here if using `state.session.async_restart()`...?
            # `state.session.async_restart()` throws `RPCException` if something goes wrong.
            pass
        # Cancellation should not fail just because interrupt delivery failed.
        pass


def _operation_state(command_id: str) -> CommandState:
    op = _get_operation(_RUNNING_COMMANDS, command_id)
    if op is None:
        op = _get_operation(_COMPLETED_COMMANDS, command_id)

    if op is None:
        return CommandState(
            command=None,
            broadcast=False,
            path=None,
            timeout=None,
            command_id=command_id,
            session_id=_parse_session_id(command_id),
            status="not_found",
            output=f"No command operation found for id: {command_id}",
            is_done=True,
        )

    if not op.task.done():
        return CommandState(
            command_id=op.command_id,
            session_id=op.session_id,
            status="running",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output="Command is still running.",
            is_done=False,
        )

    try:
        result = op.task.result()
    except asyncio.InvalidStateError:
        return CommandState(
            command_id=command_id,
            session_id=_parse_session_id(command_id),
            status="running",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output="Result not yet available.",
            is_done=False,
        )
    except asyncio.CancelledError:
        return CommandState(
            command_id=op.command_id,
            session_id=op.session_id,
            status="cancelled",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output="Command was cancelled.",
            is_done=True,
        )
    except TimeoutError as exc:
        return CommandState(
            command_id=op.command_id,
            session_id=op.session_id,
            status="timeout",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output=str(exc),
            is_done=True,
        )
    except Exception as exc:
        return CommandState(
            command_id=op.command_id,
            session_id=op.session_id,
            status="error",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output=str(exc),
            is_done=True,
        )

    return CommandState(
        command_id=op.command_id,
        session_id=op.session_id,
        status=result.status,
        command=result.command,
        broadcast=result.broadcast,
        path=result.path,
        timeout=result.timeout,
        output=result.output,
        is_done=True,
    )


def _operation_state_and_archive_if_done(command_id: str) -> CommandState:
    state = _operation_state(command_id)
    if state.is_done:
        _archive_completed_operation(command_id)
    return state


def _start_command_operation(
    command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: SupportsSessionId
) -> CommandState:
    session_id = ctx.session_id
    command_id = _build_command_id(uuid4().hex, session_id)
    task = asyncio.create_task(
        _send_command(command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx),
        name=f"pyterm-mcp:{command_id}",
    )
    task.add_done_callback(lambda _: _archive_completed_operation(command_id))
    op = CommandOperation(
        command_id=command_id,
        session_id=session_id,
        command=command,
        path=path,
        broadcast=broadcast,
        timeout=timeout,
        task=task,
    )
    _store_operation(_RUNNING_COMMANDS, op)
    return _operation_state(command_id)


async def _send_command(command, *, path=None, broadcast=False, timeout=10.0, ctx: SupportsSessionId) -> CommandResult:
    state = await _get_state_for_session(ctx.session_id)

    try:
        output = await state.run_command(command=command, broadcast=broadcast, path=path, timeout=timeout)
        return CommandResult(
            status=_configure_status(output.status),
            command=output.status.command if output.status and output.status.command else command,
            broadcast=broadcast,
            path=path,
            timeout=timeout,
            output=output.output,
        )
    except Exception as e:
        return CommandResult(
            status="error",
            command=command,
            broadcast=broadcast,
            path=path,
            timeout=timeout,
            output=str(e),
        )


@mcp.tool(title="Send Command", description="Send a command to the user's terminal.")
async def send_command(
    command: str,
    ctx: Context,
    path: str | None = None,
    broadcast: bool = False,
    timeout: float = 10.0,
    response_timeout: float | None = None,
) -> CallToolResult:
    """
    Send a command to the user's terminal and return the output.

    For commands that may hang or need lifecycle control, prefer:
    start_command -> get_command_status -> cancel_command/resend_command.
    """
    response_wait = response_timeout if response_timeout is not None else timeout + 1.0
    state = _start_command_operation(command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx)
    op = _get_operation(_RUNNING_COMMANDS, state.command_id)
    if op is None:
        state = _operation_state(state.command_id)
        return CallToolResult(
            content=[TextContent(type="text", text=state.output)],
            structuredContent=state.model_dump(),
            isError=True,
        )

    done, _ = await asyncio.wait({op.task}, timeout=response_wait)

    if done:
        state = _operation_state_and_archive_if_done(state.command_id)
        return CallToolResult(
            content=[TextContent(type="text", text=state.output)],
            structuredContent=state.model_dump(),
            isError=(state.status != "success"),
        )

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    "Command is still running. "
                    f"Use get_command_status, cancel_command, or resend_command with command_id={state.command_id}."
                ),
            )
        ],
        structuredContent=state.model_dump(),
        isError=False,
    )


@mcp.tool(title="Start Command", description="Start a terminal command and return a command id.")
async def start_command(
    command: str,
    ctx: Context,
    path: str | None = None,
    broadcast: bool = False,
    timeout: float = 10.0,
) -> CommandState:
    """
    Start a command without waiting for completion.

    Use this when a command may hang, produce delayed output, or need user-controlled
    cancellation/resend behavior.
    """
    return _start_command_operation(command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx)


@mcp.tool(title="Get Command Status", description="Get the status/output for a started command.")
async def get_command_status(command_id: str) -> CommandState:
    """Return the current state of a previously started command."""
    return _operation_state_and_archive_if_done(command_id)


@mcp.tool(title="Cancel Command", description="Cancel a running terminal command.")
async def cancel_command(ctx: Context, command_id: str | None = None) -> CommandState:
    """Cancel either one or all running commands.

    ---

    :param command_id: The ID of the running command. If ``None``, cancels all running commands, defaults to None
    :type command_id: ``str | None``, optional
    :return: State returned by command lifecycle/control tools.
    :rtype: :class:`CommandState`
    """
    if command_id is None:
        cmd_ids = _iter_command_ids(_RUNNING_COMMANDS)

        for cmd_id in cmd_ids:
            await cancel_command(ctx, cmd_id)

        return CommandState(
            command=None,
            session_id=ctx.session_id,
            broadcast=False,
            path=None,
            timeout=None,
            command_id=", ".join(cmd_ids),
            status="cancelled",
            output=f"{len(cmd_ids)} running commands have been cancelled.",
            is_done=True,
        )

    op = _get_operation(_RUNNING_COMMANDS, command_id)

    if op is None:
        return _operation_state(command_id)

    if not op.task.done():
        op.task.cancel()
        await _interrupt_terminal(broadcast=op.broadcast, session_id=op.session_id)

        with suppress(asyncio.CancelledError):
            await op.task

    return _operation_state_and_archive_if_done(command_id)


@mcp.tool(title="Resend Command", description="Cancel and resend a previous command.")
async def resend_command(ctx: Context, command_id: str, cancel_existing: bool = True) -> CommandState:
    """
    Resend a command using the original command/path/broadcast/timeout settings.

    By default this cancels the existing operation first.
    """
    running_op = _get_operation(_RUNNING_COMMANDS, command_id)
    op = running_op or _get_operation(_COMPLETED_COMMANDS, command_id)
    if op is None:
        return _operation_state(command_id)

    if running_op is not None and running_op.task.done():
        _archive_completed_operation(command_id)
    elif cancel_existing and running_op is not None:
        await cancel_command(ctx, command_id)

    return _start_command_operation(op.command, path=op.path, broadcast=op.broadcast, timeout=op.timeout, ctx=ctx)


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    PrettyLog.get_logger("iterm2_api_wrapper").disable()
    main()
