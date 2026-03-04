from __future__ import annotations

import asyncio

from iterm2_api_wrapper.client import get_shared_client
from iterm2_api_wrapper._logging import PrettyLog
from mcp.server.fastmcp import FastMCP

from pyterm_mcp.return_types import CommandResult


mcp = FastMCP(name="PyTerm-MCP")


async def _send_command(command, path=None, broadcast=False, timeout=10.0):
    client = await get_shared_client()
    state = await client.get_state_async()
    try:
        output = await state.run_command(
            command=command, broadcast=broadcast, path=path, timeout=timeout
        )
        return CommandResult(
            status="success",
            command=command,
            output=output.strip() if output else "(no output)",
        )
    except Exception as e:
        return CommandResult(
            status="error", command=command, output=str(e)
        )


@mcp.tool(
    title="Send Command",
    description="Send a command to the user's terminal.",
    structured_output=None,
)
async def send_command(
    command: str, path: str | None = None, broadcast: bool = False, timeout: float = 10.0
) -> CommandResult:
    """
    Send a command to the user's terminal and return the output.

    .. NOTE::
        The `path` parameter specifies the working directory in which to run the command.
        If the command needs to be run in a specific directory (e.g., activating a virtual
        environment), you must provide the desired path here.

    ---

    :param command: The command to send to the terminal.
    :type command: ``str``
    :param path: The working directory in which to run the command, defaults to None
    :type path: ``str``, optional
    :param broadcast: Whether to broadcast the command to all sessions, defaults to False
    :type broadcast: ``bool``, optional
    :param timeout: The timeout for the command execution, defaults to 10.0
    :type timeout: ``float``, optional
    :return: The result of the command execution.
    :rtype: ``CommandResult``
    """
    result = await _send_command(command, path=path, broadcast=broadcast, timeout=timeout)
    return result


def main() -> None:
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    PrettyLog.get_logger("iterm2_api_wrapper").disable()
    main()
