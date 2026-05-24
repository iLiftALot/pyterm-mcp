"""Console script for pyterm_mcp."""

import shlex
import subprocess
from pathlib import Path
from typing import IO, Annotated, Any

import typer
from rich.console import Console


app = typer.Typer()
console = Console()
main_file = Path(__file__).parent / "main.py"


def _run_cmd(
    arg_string: str,
    shell: bool = False,
    check: bool | None = None,
    cwd: str = str(main_file.parents[2]),
    stdout: int | IO[Any] | None = None,
    stderr: int | IO[Any] | None = None
):
    args_formatted: list[str] | str = (
        arg_string if shell is True else shlex.split(arg_string)
    )
    kwargs = {
        "shell": shell,
        "check": check if check is not None else not shell,
        "stdout": stdout,
        "stderr": stderr,
        "cwd": cwd,
    }

    try:
        subprocess.run(args_formatted, **kwargs)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error:[/red] {e}")
    except KeyboardInterrupt:
        console.print("\n[yellow]MCP inspector stopped.[/yellow]")


@app.command()
def inspect():
    # Kill any existing MCP inspector processes on both ports
    for port in (6274, 6277):
        _run_cmd(
            f"lsof -ti :{port} | xargs kill -9",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    _run_cmd(f"fastmcp dev inspector {main_file.relative_to(Path.cwd())!s}")


@app.command()
def generate_cli(
    spec: Annotated[str, typer.Argument()] = "src/pyterm_mcp/main.py",
    output: Annotated[str, typer.Argument()] = "src/pyterm_mcp/auto_cli.py",
    force: Annotated[bool, typer.Option("--force")] = False,
):
    base_arguments = "fastmcp generate-cli"
    arg_string = f"{base_arguments}{' -f ' if force is True else ' '}--server-spec {spec} --output {output}"
    _run_cmd(arg_string)


if __name__ == "__main__":
    app()
