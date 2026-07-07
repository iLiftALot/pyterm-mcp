# Justfile for pyterm-mcp

set export
set positional-arguments
set shell := ["/bin/zsh", "-c"]
set unstable := true

PYTHONPATH := ""
PYTHONTRACEMALLOC := "1"

# Show available commands
list:
    @just --list

format:
    uv run --active --python=3.10 --group dev ruff format --verbose . 2&>/dev/null | rg --pcre2 '(?!^\[\d{4})(.*)' --only-matching --colors=match:none --colors=path:fg:green --colors=highlight:none

lint:
    uv run --active --python=3.10 --group dev ruff check --fix --show-fixes --no-force-exclude --verbose . 2&>/dev/null | rg --pcre2 '(?!^\[\d{4})(.*)' --only-matching --colors=match:none --colors=path:fg:green --colors=highlight:none

# Run all the formatting, linting, and testing commands
qa:
    uv run --active --python=3.10 --group dev ruff format .
    uv run --active --python=3.10 --group dev ruff check . --fix
    uv run --active --python=3.10 --group dev ruff check --select I --fix .
    uv run --active --python=3.10 --group dev pytest . --

test:
    uv run --active --python=3.10 --group dev pytest .

# Run all the tests for all the supported Python versions
test-pyversions:
    uv run --active --python=3.10 --group dev pytest
    uv run --active --python=3.11 --group dev pytest
    uv run --active --python=3.12 --group dev pytest
    uv run --active --python=3.13 --group dev pytest
    uv run --active --python=3.14 --group dev pytest
    uv run --active --python=3.15 --group dev pytest

# Run all the tests, but allow for arguments to be passed
test-args *ARGS:
    #!/usr/bin/env zsh
    CMD_ARGS="run --active --python=3.10 --group dev pytest"
    if [[ -z "{{ARGS}}" ]]; then
        CMD_ARGS="$CMD_ARGS ."
    else
        CMD_ARGS="$CMD_ARGS {{ARGS}}"
    fi

    echo "{{YELLOW + BOLD}}>>> uv{{NORMAL}} {{GREEN + UNDERLINE}}$CMD_ARGS{{NORMAL}}";
    uv ${=CMD_ARGS}

# Run all the tests, but on failure, drop into the debugger
test-debug *ARGS:
    @echo "Running with arg: {{ARGS}}"
    uv run --active --python=3.10 --group dev pytest --pdb --maxfail=10 --pdbcls=IPython.terminal.debugger:TerminalPdb {{ARGS}}

# Run coverage, and build to HTML
[arg("open", long, short="o", value="true", help="Open HTML report?")]
test-coverage open="false":
    #!/usr/bin/env zsh
    if [[ "{{open}}" == "true" ]]; then
        uv run --active --python=3.10 --group dev pytest . --cov=pyterm_mcp --cov-report=term-missing --cov-report=html  --show
    else
        uv run --active --python=3.10 --group dev pytest . --cov=pyterm_mcp --cov-report=term-missing --cov-report=html
    fi

# Build and sync the project, useful for checking that packaging is correct
build:
    uv sync --active
    rm -rf build
    rm -rf dist
    uv build
    uv build-backend build-sdist .
    uv build-backend build-wheel .
    uv build-backend build-editable .
    uv sync --active
    uv tool uninstall pyterm_mcp 2>/dev/null || echo "❌ '{{BOLD + ITALIC + RED}}pyterm_mcp{{NORMAL}}' {{RED}}executable is {{UNDERLINE + BOLD}}not yet installed{{NORMAL}}.\n⏳{{GREEN}}Installing now...{{NORMAL}}"
    uv tool install . --editable

VERSION := "$(uv version --active --short)"

# Print the current version of the project
version:
    @echo "Current version is {{VERSION}}"

tag:
    echo "Tagging v{{VERSION}} locally."
    git tag -a v{{VERSION}} -m "Creating version v{{VERSION}}"

# Tag the current version in git and put to github
[confirm("Upload the current tag to GitHub?")]
tag-publish:
    @just tag
    git push origin v{{VERSION}}

# remove build artifacts
clean-build:
    rm -fr build/
    rm -fr dist/
    rm -fr .eggs/
    find . -name '*.egg-info' -type d -exec rm -fr {} +
    find . -name '*.egg' -type f -exec rm -f {} +

# remove Python file artifacts
clean-pyc:
    find . -name '*.pyc' -type f -exec rm -f {} +
    find . -name '*.pyo' -type f -exec rm -f {} +
    find . -name '*~' -type f -exec rm -f {} +
    find . -name '__pycache__' -type d -exec rm -fr {} +

# remove linter artifacts
clean-linter:
    find . -name '.mypy_cache' -type d -exec rm -rf {} +
    find . -name '.ruff_cache' -type d -exec rm -rf {} +

# remove test and coverage artifacts
clean-test:
    rm -f .coverage
    rm -fr htmlcov/
    rm -fr .pytest_cache

# Clean docs/_build
clean-docs:
    rm -rf docs/_build

# remove all build, mypy, test, coverage, docs, and Python artifacts
clean:
    @just clean-build
    @just clean-pyc
    @just clean-linter
    @just clean-test
    @just clean-docs

# Build docs
docs:
    uv run sphinx-build -b html docs docs/_build

# Open docs in browser
docs-open:
    open docs/_build/index.html


# Watch for changes and auto-rebuild (requires sphinx-autobuild)
docs-watch:
    uv run sphinx-autobuild docs docs/_build --open-browser
