"""Console script for pyterm_mcp."""

import subprocess
from pathlib import Path

import typer
from rich.console import Console


app = typer.Typer()
console = Console()
main_file = Path(__file__).parent / "main.py"


@app.command()
def main():
    try:
        # Kill any existing MCP inspector processes on both ports
        for port in (6274, 6277):
            subprocess.run(
                f"lsof -ti :{port} | xargs kill -9",
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        subprocess.run(
            ["mcp", "dev", str(main_file.relative_to(Path.cwd()))],
            check=True,
            cwd=str(main_file.parents[2]),
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error:[/red] {e}")
    except KeyboardInterrupt:
        console.print("\n[yellow]MCP inspector stopped.[/yellow]")


if __name__ == "__main__":
    app()
