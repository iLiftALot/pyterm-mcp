from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from fastmcp import Context, FastMCP
from iterm2.rpc import RPCException
from iterm2_api_wrapper._logging import PrettyLog
from iterm2_api_wrapper.client import get_shared_client
from iterm2_api_wrapper.typings import CommandExecutionStatus
from mcp.types import CallToolResult, TextContent

from pyterm_mcp.types import CommandOperation, CommandResult, CommandState, CommandStatus


mcp = FastMCP(
    name="PyTerm-MCP",
    instructions="A tool to interact with the user's terminal. Use this tool to run commands in the user's environment and get the output.",
)
_RUNNING_COMMANDS: dict[str, CommandOperation] = {}


def _parse_session_id(command_id: str) -> str:
    return command_id.split(":")[1]


def _build_command_id(command_id: str, session_id: str) -> str:
    return f"{command_id}:{session_id}"


def _configure_status(status: CommandExecutionStatus | None) -> CommandStatus:
    if status is None:
        return "unknown (Shell-Integration Disabled)"
    return "success" if status.succeeded else "error"


async def _interrupt_terminal(*, broadcast: bool) -> None:
    """Send Ctrl-C to the active terminal. Best-effort: never fail control tools."""
    try:
        client = await get_shared_client()
        state = await client.get_state_async()
        await state.send_escape_sequence("CNTRL_C", "CNTRL_C", broadcast=broadcast)
    except Exception as e:
        if isinstance(e, RPCException):
            # TODO: Maybe do something here if using `state.session.async_restart()`...?
            # `state.session.async_restart()` throws `RPCException` if something goes wrong.
            pass
        # Cancellation should not fail just because interrupt delivery failed.
        pass


def _operation_state(command_id: str) -> CommandState:
    op = _RUNNING_COMMANDS.get(command_id)
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


def _start_command_operation(
    command: str, *, path: str | None, broadcast: bool, timeout: float, ctx: Context
) -> CommandState:
    session_id = ctx.session_id
    command_id = _build_command_id(uuid4().hex, session_id)
    task = asyncio.create_task(
        _send_command(command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx),
        name=f"pyterm-mcp:{command_id}",
    )
    _RUNNING_COMMANDS[command_id] = CommandOperation(
        command_id=command_id,
        session_id=session_id,
        command=command,
        path=path,
        broadcast=broadcast,
        timeout=timeout,
        task=task,
    )
    return _operation_state(command_id)


async def _send_command(
    command, *, path=None, broadcast=False, timeout=10.0, ctx: Context
) -> CommandResult:
    client = await get_shared_client(service_name="pyterm-mcp", extra_id=ctx.session_id)
    state = await client.get_state_async()
    try:
        output = await state.run_command(
            command=command, broadcast=broadcast, path=path, timeout=timeout
        )
        return CommandResult(
            status=_configure_status(output.status),
            command=output.status.command
            if output.status and output.status.command
            else command,
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
    state = _start_command_operation(
        command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx
    )
    op = _RUNNING_COMMANDS[state.command_id]
    done, _ = await asyncio.wait({op.task}, timeout=response_wait)

    if done:
        state = _operation_state(state.command_id)
        del op
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


@mcp.tool(
    title="Start Command", description="Start a terminal command and return a command id."
)
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
    return _start_command_operation(
        command, path=path, broadcast=broadcast, timeout=timeout, ctx=ctx
    )


@mcp.tool(
    title="Get Command Status", description="Get the status/output for a started command."
)
async def get_command_status(command_id: str) -> CommandState:
    """Return the current state of a previously started command."""
    return _operation_state(command_id)


@mcp.tool(title="Cancel Command", description="Cancel a running terminal command.")
async def cancel_command(ctx: Context, command_id: str | None = None) -> CommandState:
    """Cancel a running command.

    ---

    :param command_id: The ID of the running command. If ``None``, cancels all running commands, defaults to None
    :type command_id: ``str | None``, optional
    :return: State returned by command lifecycle/control tools.
    :rtype: :class:`CommandState`
    """
    if command_id is None:
        cmd_ids = list(_RUNNING_COMMANDS)

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

    op = _RUNNING_COMMANDS.get(command_id)

    if op is None:
        return _operation_state(command_id)

    if not op.task.done():
        op.task.cancel()
        await _interrupt_terminal(broadcast=op.broadcast)

        with suppress(asyncio.CancelledError):
            await op.task

    del op
    return _operation_state(command_id)


@mcp.tool(title="Resend Command", description="Cancel and resend a previous command.")
async def resend_command(
    ctx: Context, command_id: str, cancel_existing: bool = True
) -> CommandState:
    """
    Resend a command using the original command/path/broadcast/timeout settings.

    By default this cancels the existing operation first.
    """
    op = _RUNNING_COMMANDS.get(command_id)
    if op is None:
        return _operation_state(command_id)

    if cancel_existing and not op.task.done():
        await cancel_command(ctx, command_id)

    return _start_command_operation(
        op.command, path=op.path, broadcast=op.broadcast, timeout=op.timeout, ctx=ctx
    )


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    PrettyLog.get_logger("iterm2_api_wrapper").disable()
    main()
