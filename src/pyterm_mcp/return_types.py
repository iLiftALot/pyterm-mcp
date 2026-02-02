from pydantic import BaseModel


class CommandResult(BaseModel):
    status: str
    command: str
    broadcast: bool
    output: str
