import asyncio
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, Field


type CommandStatus = Literal[
    "running", "success", "error", "unknown (Shell-Integration Disabled)", "timeout", "cancelled", "not_found"
]


class SupportsSessionId(Protocol):
    @property
    def session_id(self) -> str: ...


class CommandResult(BaseModel):
    status: CommandStatus = Field(
        ...,
        description="The status of the command execution, either 'success' or 'error'.",
    )
    command: str = Field(..., description="The command that was executed.")
    broadcast: bool = Field(..., description="Whether the command was broadcast to all sessions.")
    path: str | None = Field(None, description="The working directory in which the command was executed.")
    timeout: float = Field(..., description="The timeout for the command execution.")
    output: str = Field(..., description="The output of the command execution.")


class CommandState(BaseModel):
    """State returned by command lifecycle/control tools."""

    command_id: str = Field(..., description="Opaque command operation id.")
    session_id: str = Field(..., description="Opaque session operation id.")
    status: CommandStatus = Field(..., description="Current command operation status.")
    command: str | None = Field(None, description="Command associated with this operation.")
    broadcast: bool = Field(False, description="Whether the command was broadcast.")
    path: str | None = Field(None, description="Working directory used for the command.")
    timeout: float | None = Field(None, description="Command execution timeout.")
    output: str = Field("", description="Latest output, error, or status message.")
    is_done: bool = Field(False, description="Whether the operation reached a terminal state.")


@dataclass(slots=True)
class CommandOperation:
    command_id: str
    session_id: str
    command: str
    path: str | None
    broadcast: bool
    timeout: float
    task: asyncio.Task[CommandResult]


@dataclass(slots=True)
class CommandSession:
    session_id: str
    operations: dict[str, CommandOperation] = field(default_factory=dict)
