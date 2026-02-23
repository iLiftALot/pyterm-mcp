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
    output: str = Field(..., description="The output of the command execution.")
