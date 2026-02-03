from __future__ import annotations

import asyncio
from typing import Callable, ParamSpec, TypeVar

from iterm2_api_wrapper.client import create_iterm_client, iTermClient
from iterm2_api_wrapper.state import iTermState
from mcp.server.fastmcp import FastMCP

from pyterm_mcp.return_types import CommandResult


mcp = FastMCP(name="PyTerm-MCP")
_client: iTermClient | None = None


def _get_client() -> iTermClient[iTermState]:
    global _client
    if _client is None:
        _client = create_iterm_client()
        return _client

    loop = _client.loop
    if loop.is_closed() or not loop.is_running():
        try:
            _client.close()
        except Exception:
            pass
        _client = create_iterm_client()
    return _client


T = TypeVar("T")
P = ParamSpec("P")


async def _run_in_thread(fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    return await asyncio.to_thread(fn, *args, **kwargs)


def _send_command(
    command: str, path: str | None = None, broadcast: bool = False, timeout: float = 10.0
) -> CommandResult:
    """Blocking helper that runs in the client's event loop."""
    client = _get_client()

    async def inner() -> CommandResult:
        async with client.state_manager_async(close=False) as state:
            try:
                output = await state.run_command(
                    command=command, broadcast=broadcast, path=path, timeout=timeout
                )
                return CommandResult(
                    status="success",
                    command=command,
                    broadcast=broadcast,
                    output=output.strip() if output else "(no output)",
                )
            except Exception as e:
                return CommandResult(
                    status="error", command=command, broadcast=broadcast, output=str(e)
                )

    return asyncio.run_coroutine_threadsafe(inner(), client._loop).result()


@mcp.tool(
    title="Send Command",
    description="Send a command to the user's terminal.",
    structured_output=True,
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
    result = await _run_in_thread(
        _send_command, command, path=path, broadcast=broadcast, timeout=timeout
    )
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
