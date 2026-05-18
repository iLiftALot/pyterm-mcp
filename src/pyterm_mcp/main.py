from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

from iterm2.rpc import RPCException
from iterm2_api_wrapper._logging import PrettyLog
from iterm2_api_wrapper.client import get_shared_client
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from pyterm_mcp.types import CommandOperation, CommandResult, ManagedCommandState


mcp = FastMCP(
    name="PyTerm-MCP",
    instructions="A tool to interact with the user's terminal. Use this tool to run commands in the user's environment and get the output.",
)
_RUNNING_COMMANDS: dict[str, CommandOperation] = {}


async def _best_effort_interrupt_terminal(*, broadcast: bool) -> None:
    """Send Ctrl-C to the active terminal. Best-effort: never fail control tools."""
    try:
        client = await get_shared_client()
        state = await client.get_state_async()
        # await state.session.async_stop_coprocess()
        # await state.session.async_run_coprocess("...")
        # await state.session.async_restart()
        await state.session.async_send_text("\x03", suppress_broadcast=not broadcast)
    except (Exception, RPCException) as e:
        if isinstance(e, RPCException):
            # TODO: Maybe do something here...?
            # `state.session.async_restart()` throws `RPCException` if something goes wrong.
            pass
        # Cancellation should not fail just because interrupt delivery failed.
        pass


def _operation_state(command_id: str) -> ManagedCommandState:
    op = _RUNNING_COMMANDS.get(command_id)
    if op is None:
        return ManagedCommandState(
            command=None,
            broadcast=False,
            path=None,
            timeout=None,
            command_id=command_id,
            status="not_found",
            output=f"No command operation found for id: {command_id}",
            is_done=True,
        )

    if not op.task.done():
        return ManagedCommandState(
            command_id=op.command_id,
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
    except asyncio.CancelledError:
        return ManagedCommandState(
            command_id=op.command_id,
            status="cancelled",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output="Command was cancelled.",
            is_done=True,
        )
    except TimeoutError as exc:
        return ManagedCommandState(
            command_id=op.command_id,
            status="timeout",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output=str(exc),
            is_done=True,
        )
    except Exception as exc:
        return ManagedCommandState(
            command_id=op.command_id,
            status="error",
            command=op.command,
            broadcast=op.broadcast,
            path=op.path,
            timeout=op.timeout,
            output=str(exc),
            is_done=True,
        )

    return ManagedCommandState(
        command_id=op.command_id,
        status=result.status,
        command=result.command,
        broadcast=result.broadcast,
        path=result.path,
        timeout=result.timeout,
        output=result.output,
        is_done=True,
    )


def _start_command_operation(
    command: str, *, path: str | None, broadcast: bool, timeout: float
) -> ManagedCommandState:
    command_id = uuid4().hex
    task = asyncio.create_task(
        _send_command(command, path=path, broadcast=broadcast, timeout=timeout),
        name=f"pyterm-mcp:{command_id}",
    )
    _RUNNING_COMMANDS[command_id] = CommandOperation(
        command_id=command_id,
        command=command,
        path=path,
        broadcast=broadcast,
        timeout=timeout,
        task=task,
    )
    return _operation_state(command_id)


async def _send_command(
    command, path=None, broadcast=False, timeout=10.0
) -> CommandResult:
    client = await get_shared_client()
    state = await client.get_state_async()
    try:
        output = await state.run_command(
            command=command, broadcast=broadcast, path=path, timeout=timeout
        )
        return CommandResult(
            status="success",
            command=command,
            broadcast=broadcast,
            path=path,
            timeout=timeout,
            output=output.strip() if output else "<no output>",
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
        command, path=path, broadcast=broadcast, timeout=timeout
    )

    op = _RUNNING_COMMANDS[state.command_id]
    done, _ = await asyncio.wait({op.task}, timeout=response_wait)

    if done:
        state = _operation_state(state.command_id)
        return CallToolResult(
            content=[TextContent(type="text", text=state.output)]
            if state.status == "success"
            else [],
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
    command: str, path: str | None = None, broadcast: bool = False, timeout: float = 10.0
) -> ManagedCommandState:
    """
    Start a command without waiting for completion.

    Use this when a command may hang, produce delayed output, or need user-controlled
    cancellation/resend behavior.
    """
    return _start_command_operation(
        command, path=path, broadcast=broadcast, timeout=timeout
    )


@mcp.tool(
    title="Get Command Status", description="Get the status/output for a started command."
)
async def get_command_status(command_id: str) -> ManagedCommandState:
    """Return the current state of a previously started command."""
    return _operation_state(command_id)


@mcp.tool(title="Cancel Command", description="Cancel a running terminal command.")
async def cancel_command(
    command_id: str | None, interrupt_terminal: bool = True
) -> ManagedCommandState:
    """
    Cancel a running command.

    If interrupt_terminal is true, PyTerm-MCP also sends Ctrl-C to the terminal as a
    best-effort attempt to stop the shell process.
    """
    if command_id is None:
        cmd_ids = list(_RUNNING_COMMANDS)

        for cmd_id in cmd_ids:
            to_cancel: ManagedCommandState = await cancel_command(cmd_id, interrupt_terminal=interrupt_terminal)

            if to_cancel.is_done is True:
                del _RUNNING_COMMANDS[cmd_id]

        return ManagedCommandState(
            command=None,
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
        was_cancelled = op.task.cancel()
        if interrupt_terminal:
            await _best_effort_interrupt_terminal(broadcast=op.broadcast)

        with suppress(asyncio.CancelledError):
            await op.task

        if was_cancelled:
            del _RUNNING_COMMANDS[command_id]

    return _operation_state(command_id)


@mcp.tool(title="Resend Command", description="Cancel and resend a previous command.")
async def resend_command(
    command_id: str, cancel_existing: bool = True
) -> ManagedCommandState:
    """
    Resend a command using the original command/path/broadcast/timeout settings.

    By default this cancels the existing operation first.
    """
    op = _RUNNING_COMMANDS.get(command_id)
    if op is None:
        return _operation_state(command_id)

    if cancel_existing and not op.task.done():
        await cancel_command(command_id)

    return _start_command_operation(
        op.command, path=op.path, broadcast=op.broadcast, timeout=op.timeout
    )


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    PrettyLog.get_logger("iterm2_api_wrapper").disable()
    main()
