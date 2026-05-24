import asyncio
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    status: Literal["success", "error"] = Field(
        ...,
        description="The status of the command execution, either 'success' or 'error'.",
    )
    command: str = Field(..., description="The command that was executed.")
    broadcast: bool = Field(
        ..., description="Whether the command was broadcast to all sessions."
    )
    path: str | None = Field(
        None, description="The working directory in which the command was executed."
    )
    timeout: float = Field(..., description="The timeout for the command execution.")
    output: str = Field(..., description="The output of the command execution.")


type CommandStatus = Literal[
    "running", "success", "error", "timeout", "cancelled", "not_found"
]


class CommandState(BaseModel):
    """State returned by command lifecycle/control tools."""

    command_id: str = Field(..., description="Opaque command operation id.")
    status: CommandStatus = Field(..., description="Current command operation status.")
    command: str | None = Field(
        None, description="Command associated with this operation."
    )
    broadcast: bool = Field(False, description="Whether the command was broadcast.")
    path: str | None = Field(None, description="Working directory used for the command.")
    timeout: float | None = Field(None, description="Command execution timeout.")
    output: str = Field("", description="Latest output, error, or status message.")
    is_done: bool = Field(
        False, description="Whether the operation reached a terminal state."
    )


@dataclass(slots=True)
class CommandOperation:
    command_id: str
    command: str
    path: str | None
    broadcast: bool
    timeout: float
    task: asyncio.Task[CommandResult]
