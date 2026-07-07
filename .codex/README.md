# Environment Tool Generation

## Codex Desktop Local Environment Generation Flow

1. `.codex/environments/environment.toml`
2. setup script runs `uv sync --dev --python=3.x`
3. creates `.codex/bin/python` and `.codex/bin/python3` symlinks
4. writes `.codex/env.sh`
5. Install Project Executables action sources `.codex/env.sh`
6. `>>> uv tool install --force --editable . --project=. --python=./.venv/bin/python`
7. `UV_TOOL_DIR` sends tool venv/state to `.codex/tools`
8. `UV_TOOL_BIN_DIR` sends command shims to `.codex/bin`
9. `.codex/bin/<tool_executable_name>` and `.codex/bin/<package-name>` symlink into `<package-name>/.codex/tools/iterm2-api-wrapper/bin/`

## Instructions

1. Create [\<package-name\>/.codex/env.sh](./env.sh)

    ```sh
    export PATH="$PWD/.codex/bin:$PWD/.venv/bin:$PATH"
    export UV_CACHE_DIR="/private/tmp/<package-name>-uv-cache"
    export UV_TOOL_DIR="$PWD/.codex/tools"
    export UV_TOOL_BIN_DIR="$PWD/.codex/bin"
    # ... any other variables ....
    ```

2. Run the following commands from the repository root

    ```sh
    source .codex/env.sh
    uv tool install --force --editable . --project=. --python=./.venv/bin/python
    ```
