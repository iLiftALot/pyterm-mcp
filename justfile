# Justfile for pyterm-mcp

# Show available commands
list:
    @just --list

# Run all the formatting, linting, and testing commands
qa:
    uv run --python=3.12 --extra test ruff format .
    uv run --python=3.12 --extra test ruff check . --fix
    uv run --python=3.12 --extra test ruff check --select I --fix .
    uv run --python=3.12 --extra test ty check .
    uv run --python=3.12 --extra test pytest .

# Run all the tests for all the supported Python versions
testall:
    uv run --python=3.10 --extra test pytest
    uv run --python=3.11 --extra test pytest
    uv run --python=3.12 --extra test pytest
    uv run --python=3.13 --extra test pytest

# Run all the tests, but allow for arguments to be passed
test *ARGS:
    @echo "Running with arg: {{ARGS}}"
    uv run --python=3.13 --extra test pytest {{ARGS}}

# Run all the tests, but on failure, drop into the debugger
pdb *ARGS:
    @echo "Running with arg: {{ARGS}}"
    uv run --python=3.13  --extra test pytest --pdb --maxfail=10 --pdbcls=IPython.terminal.debugger:TerminalPdb {{ARGS}}

# Run coverage, and build to HTML
coverage:
    uv run --python=3.13 --extra test coverage run -m pytest .
    uv run --python=3.13 --extra test coverage report -m
    uv run --python=3.13 --extra test coverage html

# Build the project, useful for checking that packaging is correct
build:
    uv sync --active
    rm -rf build
    rm -rf dist
    uv build
    uv build-backend build-sdist .
    uv build-backend build-wheel .
    uv build-backend build-editable .
    uv sync --active

build-install:
    @just build
    uv tool uninstall pyterm-mcp
    uv tool install . --editable

VERSION := `grep -m1 '^version' pyproject.toml | sed -E 's/version = "(.*)"/\1/'`

# Print the current version of the project
version:
    @echo "Current version is {{VERSION}}"

# Tag the current version in git and put to github
tag:
    echo "Tagging version v{{VERSION}}"
    git tag -a v{{VERSION}} -m "Creating version v{{VERSION}}"
    git push origin v{{VERSION}}

# remove build artifacts
clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

# remove Python file artifacts
clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

# remove test and coverage artifacts
clean-test:
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

# remove all build, test, coverage and Python artifacts
clean:
    @just clean-build
    @just clean-pyc
    @just clean-test

# Build docs
docs:
    uv run sphinx-build -b html docs docs/_build

# Clean and rebuild docs
docs-clean:
    rm -rf docs/_build
    uv run sphinx-build -b html docs docs/_build

# Open docs in browser
docs-open:
    open docs/_build/index.html

# Build and open docs
docs-view: docs-clean docs-open

# Watch for changes and auto-rebuild (requires sphinx-autobuild)
docs-watch:
    uv run sphinx-autobuild docs docs/_build --open-browser
