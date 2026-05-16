"""Console script for pyterm_mcp."""

from pathlib import Path
import subprocess
import typer
from rich.console import Console


app = typer.Typer()
console = Console()
main_file = Path(__file__).parent / "main.py"


@app.command()
def main():
    try:
        cmd = subprocess.run(
            ["mcp", "dev", str(main_file.relative_to(Path.cwd()))],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(main_file.parents[2]),
        )
        console.print(cmd.stdout)
    except subprocess.CalledProcessError as e:
        console.print(
            f"[red]Error:[/red] {e.stderr if e.stderr else str(e)}\n{e.stdout if e.stdout else ''}"
        )


if __name__ == "__main__":
    app()
