"""Console script for pyterm_mcp."""

from pathlib import Path
import subprocess
import typer
import asyncio
from rich.console import Console
from iterm2_api_wrapper.client import create_iterm_client
from iterm2_api_wrapper.state import iTermState


app = typer.Typer()
console = Console()
main_file = Path(__file__).parent / "main.py"


@app.command()
def main():
    try:
        cmd = subprocess.run(
            ["mcp", "dev", str(main_file)],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(main_file.parents[2]),
        )
        console.print(cmd.stdout)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error:[/red] {e.stderr}")


if __name__ == "__main__":
    app()
