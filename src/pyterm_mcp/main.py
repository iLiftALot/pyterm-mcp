import asyncio
from iterm2_api_wrapper.client import create_iterm_client, iTermClient
from iterm2_api_wrapper.state import iTermState
from typing import Any
from mcp.server.fastmcp import FastMCP


mcp = FastMCP(name="PyTerm-MCP")

_client: iTermClient | None = None


def _get_client() -> iTermClient[iTermState]:
    global _client
    if _client is None:
        _client = create_iterm_client()
    return _client


def _run_command_sync(command: str, broadcast: bool) -> dict[str, Any]:
    """Blocking helper that runs in the client's event loop."""
    client = _get_client()

    async def inner() -> dict[str, Any]:
        async with client.state_manager_async(close=False) as state:
            output = await state.run_command(command=command, broadcast=broadcast)
            return {
                "status": "sent",
                "command": command,
                "broadcast": broadcast,
                "output": output.strip() if output else "(no output)",
            }

    return asyncio.run_coroutine_threadsafe(inner(), client._loop).result(timeout=30)


@mcp.tool()
async def send_command(command: str, broadcast: bool = False) -> dict[str, Any]:
    """Send a command to the user's terminal.

    Args:
        command: The command to run in the terminal.
        broadcast: If True, send to all iTerm sessions.

    Returns:
        dict with status, command, broadcast flag, and captured output.
    """
    return await asyncio.to_thread(_run_command_sync, command, broadcast)


def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
