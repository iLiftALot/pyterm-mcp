import io
import os
import platform
import plistlib
import re
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Generator, Literal

import pytest
from dotenv import load_dotenv
from pluggy._result import Result
from rich.color_triplet import ColorTriplet
from rich.console import Console, ConsoleRenderable, RichCast
from rich.panel import Panel
from rich.table import Table
from rich.terminal_theme import TerminalTheme
from rich.text import Text


load_dotenv()
RUN_TIMEOUT = float(os.getenv("ITERM2_INTEGRATION_TIMEOUT", "60"))
log_path_env = os.getenv("ITERM2_INTEGRATION_LOG")
log_path = (
    Path(log_path_env).expanduser().resolve()
    if log_path_env
    else Path(__file__).resolve().parents[1] / "logs" / "pytest.log"
)
html_path = log_path.with_suffix(".html")
log_path.parent.mkdir(parents=True, exist_ok=True)

# Clear log files at import time (before session starts)
log_path.write_text("")

# Plain text console for .log file
_file_console = Console(
    record=False,
    log_path=False,
    log_time=False,
    file=log_path.open("a"),
    width=100,
    force_terminal=False,
    no_color=True,
)

# Recording console for HTML export (captures with colors)
_html_buffer = io.StringIO()
_html_console = Console(
    record=True,
    log_path=False,
    log_time=False,
    file=_html_buffer,  # Write to buffer, not stdout
    width=100,
    force_terminal=True,
)

# Terminal console for colored stderr output (avoids pytest stdout capture)
_terminal_console = Console(record=False, log_path=False, log_time=False, stderr=True, width=100)

type BrowserChoice = Literal["default", "safari", "chrome", "firefox", "edge", "mozilla"]


class MultiConsole:
    """Wrapper that writes to file (plain), HTML (colored), and terminal (colored) consoles."""

    def __init__(
        self,
        file_console: Console,
        html_console: Console,
        terminal_console: Console,
        *,
        browser: BrowserChoice = "default",
    ) -> None:
        self._file = file_console
        self._html = html_console
        self._terminal = terminal_console
        self._browser = browser

    def _call_all(self, method_name: str, *args, **kwargs):
        if args:
            args = (_linkify_text(args[0]), *args[1:])
        getattr(self._file, method_name)(*args, **kwargs)
        getattr(self._html, method_name)(*args, **kwargs)
        getattr(self._terminal, method_name)(*args, **kwargs)

    def print(self, *args, **kwargs) -> None:
        self._call_all("print", *args, **kwargs)

    def rule(self, *args, **kwargs) -> None:
        self._call_all("rule", *args, **kwargs)

    def save_html(self, path: Path, show: bool = False) -> None:
        terminal_theme = _build_terminal_theme()
        html = self._html.export_html(theme=terminal_theme, clear=False, inline_styles=False)
        theme_css = _build_html_theme_css(terminal_theme)
        if theme_css:
            html = _inject_html_theme(html, theme_css)
        path.write_text(html, encoding="utf-8")
        if not show:
            return
        if self._browser == "default":
            webbrowser.open(path.as_uri(), 0, True)
        else:
            subprocess.Popen(["open", "-a", self._browser, path.as_uri()])


console: MultiConsole = MultiConsole(
    file_console=_file_console,
    html_console=_html_console,
    terminal_console=_terminal_console,
)


def _linkify_text(
    obj: Path | Text | str | ConsoleRenderable | RichCast,
) -> Text | ConsoleRenderable | RichCast:
    if isinstance(obj, Path):
        resolved = obj.expanduser().resolve()
        for base in (
            Path(__file__).resolve().parents[3],
            Path(__file__).resolve().parents[1],
        ):
            try:
                label = resolved.relative_to(base)
                break
            except ValueError:
                continue
        else:
            label = resolved
        s = f"[link={resolved.as_uri()}]{label!s}[/link]"
        return Text.from_markup(s)
    elif isinstance(obj, Text):
        s = obj.plain
    elif isinstance(obj, str):
        s = obj
    else:
        return obj  # leave rich renderables unchanged

    url_pattern = r"(https?://[^\s\n]+)"
    s = re.sub(url_pattern, lambda m: f"[link={m.group(1)}]{m.group(1)}[/link]", s)

    path_pattern = rf'({re.escape(str(Path.home()))}/[^"\'\n]*)'

    def _linkify_path(match: re.Match[str]) -> str:
        raw = match.group(1)
        stripped = raw.rstrip()
        trailing = raw[len(stripped) :]
        try:
            href = Path(stripped).as_uri()
        except ValueError:
            href = f"file://{stripped}"
        return f"[link={href}]{stripped}[/link]{trailing}"

    s = re.sub(path_pattern, _linkify_path, s)

    return Text.from_markup(s)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _triplet_to_rgb(color: ColorTriplet) -> tuple[int, int, int]:
    return (int(color.red), int(color.green), int(color.blue))


def _iterm_color_to_rgb(color: dict[str, Any]) -> tuple[int, int, int] | None:
    try:
        red = float(color.get("Red Component", 0.0))
        green = float(color.get("Green Component", 0.0))
        blue = float(color.get("Blue Component", 0.0))
    except (TypeError, ValueError):
        return None

    def _clamp(value: float) -> int:
        return max(0, min(255, round(value * 255)))

    return _clamp(red), _clamp(green), _clamp(blue)


def _iterm_color_to_hex(color: dict[str, Any]) -> str | None:
    rgb = _iterm_color_to_rgb(color)
    if rgb is None:
        return None
    return _rgb_to_hex(rgb)


def _terminal_theme_from_iterm_dict(data: dict[str, Any]) -> TerminalTheme | None:
    def _get_color(name: str) -> tuple[int, int, int] | None:
        value = data.get(name)
        if not isinstance(value, dict):
            return None
        return _iterm_color_to_rgb(value)

    background = _get_color("Background Color")
    foreground = _get_color("Foreground Color")
    if background is None or foreground is None:
        return None

    normal = [_get_color(f"Ansi {index} Color") for index in range(8)]
    if any(color is None for color in normal):
        return None
    normal_colors = [color for color in normal if color is not None]

    bright = [_get_color(f"Ansi {index} Color") for index in range(8, 16)]
    bright_colors: list[tuple[int, int, int]] | None
    if any(color is None for color in bright):
        bright_colors = None
    else:
        bright_colors = [color for color in bright if color is not None]

    return TerminalTheme(background, foreground, normal_colors, bright_colors)


def _terminal_theme_from_itermcolors(path: Path) -> TerminalTheme | None:
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception as e:
        console.print(f"[red]Failed to load iTerm colors from {path}[/red]: {e}")
        return None

    if not isinstance(data, dict):
        console.print(f"[red]Invalid iTerm colors format in {path}[/red]: {data}")
        return None

    return _terminal_theme_from_iterm_dict(data)


def _load_iterm_profile() -> dict[str, object] | None:
    if not (os.getenv("TERM_PROGRAM") == "iTerm.app" or os.getenv("ITERM_PROFILE") or os.getenv("ITERM_PROFILE_ID")):
        return None

    prefs_path = Path("~/Library/Preferences/com.googlecode.iterm2.plist").expanduser()
    if not prefs_path.exists():
        console.print(f"[red]iTerm preferences file not found at {prefs_path}[/red]")
        return None

    try:
        with prefs_path.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception as e:
        console.print(f"[red]Failed to load iTerm preferences from {prefs_path}[/red]: {e}")
        return None

    if not isinstance(data, dict):
        return None

    profiles = data.get("New Bookmarks")
    if not isinstance(profiles, list):
        return None

    profile_id = os.getenv("PYTEST_HTML_THEME_PROFILE_ID") or os.getenv("ITERM_PROFILE_ID")
    profile_name = os.getenv("PYTEST_HTML_THEME_PROFILE") or os.getenv("ITERM_PROFILE")

    if profile_id:
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("Guid") == profile_id:
                return profile

    if profile_name:
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("Name") == profile_name:
                return profile

    default_guid = data.get("Default Bookmark Guid")
    if default_guid:
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("Guid") == default_guid:
                return profile

    for profile in profiles:
        if isinstance(profile, dict):
            return profile

    return None


def _build_terminal_theme() -> TerminalTheme | None:
    theme_path = os.getenv("PYTEST_HTML_THEME_PATH")
    if theme_path:
        console.print(f"[blue]Loading terminal theme from[/blue]:\n{theme_path}")
        path = Path(theme_path).expanduser()
        if path.suffix.lower() == ".itermcolors":
            theme = _terminal_theme_from_itermcolors(path)
            if theme:
                return theme

    profile = _load_iterm_profile()
    if profile:
        console.print(f"[blue]Loading terminal theme from iTerm profile {profile.get('Name', 'unknown')}[/blue]")
        return _terminal_theme_from_iterm_dict(profile)

    return None


def _load_html_extra_css() -> str | None:
    css_path = os.getenv("PYTEST_HTML_THEME_CSS_PATH")
    if css_path:
        path = Path(css_path).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8")

    theme_path = os.getenv("PYTEST_HTML_THEME_PATH")
    if theme_path:
        console.print(f"[blue]Loading HTML extra CSS from[/blue]:\n{theme_path}")
        path = Path(theme_path).expanduser()
        if path.suffix.lower() == ".css" and path.is_file():
            return path.read_text(encoding="utf-8")

    theme_inline = os.getenv("PYTEST_HTML_THEME_CSS")
    if theme_inline:
        return theme_inline

    return None


def _theme_css_from_palette(bg: str, fg: str, link: str, panel: str, muted: str) -> str:
    return (
        ":root {\n"
        f"  --bg: {bg};\n"
        f"  --fg: {fg};\n"
        f"  --link: {link};\n"
        f"  --panel: {panel};\n"
        f"  --muted: {muted};\n"
        "}\n"
        "body {\n"
        "  background: var(--bg);\n"
        "  color: var(--fg);\n"
        "}\n"
        "a {\n"
        "  color: var(--link);\n"
        "  text-decoration: underline;\n"
        "  text-underline-offset: 2px;\n"
        "  cursor: pointer;\n"
        "}\n"
        "pre {\n"
        "  background: var(--panel);\n"
        "  color: var(--fg);\n"
        "  padding: 12px 14px;\n"
        "  border-radius: 8px;\n"
        "}\n"
        "code {\n"
        "  color: var(--fg);\n"
        "}\n"
    )


def _build_html_theme_css(theme: TerminalTheme | None) -> str | None:
    extra_css = _load_html_extra_css()
    if theme is None:
        return extra_css

    bg = _rgb_to_hex(_triplet_to_rgb(theme.background_color))
    fg = _rgb_to_hex(_triplet_to_rgb(theme.foreground_color))
    ansi_colors = theme.ansi_colors

    link_rgb = _triplet_to_rgb(ansi_colors[12])
    muted_rgb = _triplet_to_rgb(ansi_colors[8])
    panel_rgb = _triplet_to_rgb(ansi_colors[0])

    base_css = _theme_css_from_palette(bg, fg, _rgb_to_hex(link_rgb), _rgb_to_hex(panel_rgb), _rgb_to_hex(muted_rgb))

    if extra_css:
        return f"{base_css}\n{extra_css}"
    return base_css


def _inject_html_theme(html: str, css: str) -> str:
    style_block = f'\n<style id="pytest-html-theme">\n{css}\n</style>\n'
    if "</head>" in html:
        return html.replace("</head>", f"{style_block}</head>", 1)
    return f"{style_block}{html}"


def _format_duration(seconds: float) -> str:
    """Format duration in human-readable form."""
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}µs"
    elif seconds < 1:
        return f"{seconds * 1000:.1f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        mins, secs = divmod(seconds, 60)
        return f"{int(mins)}m {secs:.1f}s"


def log_var(name: str, value: object) -> None:
    """Log a variable with consistent formatting."""
    console.print(f"    [dim]│[/] [cyan]{name}[/] = [yellow]{value!r}[/]")


def log_info(message: str) -> None:
    """Log an info message with consistent formatting."""
    console.print(f"    [dim]│ {message}[/]")


def pytest_sessionstart(session: pytest.Session) -> None:
    session.name = "iTerm2 API Wrapper Tests"
    object.__setattr__(session, "start_time", time.perf_counter())


def _addoption_if_missing(parser: pytest.Parser, *names: str, **kwargs: Any) -> None:
    try:
        parser.addoption(*names, **kwargs)
    except ValueError as exc:
        if "already added" not in str(exc):
            raise


def pytest_addoption(parser: pytest.Parser) -> None:
    _addoption_if_missing(
        parser,
        "--show",
        help="Show/open HTML log file after test session completes.",
        action="store_true",
        default=False,
        dest="SHOW_HTML_LOG",
    )
    _addoption_if_missing(
        parser,
        "--browser",
        help="Browser to use for opening HTML log file after test session completes.",
        choices=["default", "safari", "chrome", "firefox", "edge", "mozilla"],
        nargs="?",
        default="safari" if sys.platform == "darwin" else "default",
        dest="BROWSER",
        action="store",
    )


@pytest.fixture(autouse=True, scope="session")
def log_session_start_and_end(request: pytest.FixtureRequest) -> Generator:
    config = request.config
    session = request.session
    session_name = getattr(session, "name", "iTerm2 API Wrapper Tests")
    start_time = getattr(session, "start_time", time.perf_counter())
    console._browser = f"{config.getoption('BROWSER', 'default')}"

    # Environment info table
    env_table = Table(show_header=False, box=None, padding=(0, 2))
    env_table.add_column("Key", style="dim")
    env_table.add_column("Value")
    env_table.add_row(
        "Python",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    env_table.add_row("Platform", platform.platform())
    env_table.add_row("Root", _linkify_text(config.rootpath))
    env_table.add_row("Log File", _linkify_text(log_path))
    env_table.add_row("HTML Log", _linkify_text(html_path))
    env_table.add_row("Config", config.inipath.name) if config.inipath else "None"
    env_table.add_row("Timeout", f"{RUN_TIMEOUT:.0f} seconds")
    env_table.add_row("Invocation Args", " ".join(["pytest", *sys.argv[1:]]))
    env_table.add_row(
        "Plugins",
        " ".join(str(p) for p in config.invocation_params.plugins) if config.invocation_params.plugins else "None",
    )

    console.print()
    console.print(
        Panel(
            env_table,
            title=f"[bold magenta]{session_name}[/]",
            subtitle=f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/]",
            border_style="magenta",
            padding=(1, 2),
        )
    )

    yield

    duration = _format_duration(time.perf_counter() - start_time)

    # Results summary
    passed = session.testscollected - session.testsfailed
    status_style = "green" if session.testsfailed == 0 else "red"

    summary = Text()
    summary.append(f"✓ {passed} passed", style="green")
    if session.testsfailed:
        summary.append(f"  ✗ {session.testsfailed} failed", style="red")
    summary.append(f"  ⏱ {duration}", style="dim")

    console.print()
    console.print(
        Panel(
            summary,
            title="[bold]Session Complete[/]",
            border_style=status_style,
            padding=(0, 2),
        )
    )
    console.print()

    # Save HTML version with colors
    console.save_html(html_path, bool(request.config.getoption("SHOW_HTML_LOG", False)))


@pytest.fixture(autouse=True, scope="module")
def log_module_start_and_end(request: pytest.FixtureRequest) -> Generator:
    module_name = request.module.__name__.replace("tests.", "")
    module_path = Path(request.module.__file__).name

    console.print()
    console.rule(f"[bold blue]{module_name}[/]", style="blue")
    console.print(f"  [dim]{module_path}[/]", justify="center")

    yield

    console.rule(style="blue")


@pytest.fixture(autouse=True, scope="function")
def log_test_start_and_end(request: pytest.FixtureRequest) -> Generator:
    assert isinstance(request.node, pytest.Function)
    test_name = request.node.originalname
    start_time = time.perf_counter()

    # Build test info
    info_parts: list[str] = []

    # Parametrize info
    if hasattr(request, "param"):
        info_parts.append(f"[cyan]param=[/]{request.param!r}")

    # Markers (only if present)
    markers = [m.name for m in request.node.iter_markers() if m.name not in ("usefixtures",)]
    if markers:
        info_parts.append(f"[yellow]markers=[/]{', '.join(markers)}")

    console.print()
    console.print(f"  [bold]▶[/] [green]{test_name}[/]", end="")
    if info_parts:
        console.print(f"  [dim]({', '.join(info_parts)})[/]")
    else:
        console.print()

    yield

    duration = _format_duration(time.perf_counter() - start_time)

    # Determine outcome
    if hasattr(request.node, "rep_call"):
        outcome = request.node.rep_call  # type: ignore[attr-defined]
        if outcome.passed:
            status = "[green]✓ PASSED[/]"
        elif outcome.failed:
            status = "[red]✗ FAILED[/]"
        else:
            status = "[yellow]⊘ SKIPPED[/]"
    else:
        status = "[green]✓[/]"

    console.print(f"    {status} [dim]({duration})[/]")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Generator:
    """Store test outcome on the item for access in fixtures."""
    outcome: Result[pytest.TestReport] = yield  # type: ignore[assignment]
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
