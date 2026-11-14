---
name: "pyterm-mcp"
description: "CLI for the pyterm-mcp MCP server. Call tools, list resources, and get prompts."
---

# pyterm-mcp CLI

## Tool Commands

### send_command

Send a command to the user's terminal.

```bash
uv run --with fastmcp python cli.py call-tool send_command --command <value> --path <value> --broadcast --timeout <value> --response-timeout <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command` | string | yes |  |
| `--path` | string | no | JSON string |
| `--broadcast` | boolean | no |  |
| `--timeout` | number | no |  |
| `--response-timeout` | string | no | JSON string |

### start_command

Start a terminal command and return a command id.

```bash
uv run --with fastmcp python cli.py call-tool start_command --command <value> --path <value> --broadcast --timeout <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command` | string | yes |  |
| `--path` | string | no | JSON string |
| `--broadcast` | boolean | no |  |
| `--timeout` | number | no |  |

### get_command_status

Get the status/output for a started command.

```bash
uv run --with fastmcp python cli.py call-tool get_command_status --command-id <value>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command-id` | string | yes |  |

### cancel_command

Cancel a running terminal command.

```bash
uv run --with fastmcp python cli.py call-tool cancel_command --command-id <value> --interrupt-terminal
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command-id` | string | yes | JSON string |
| `--interrupt-terminal` | boolean | no |  |

### resend_command

Cancel and resend a previous command.

```bash
uv run --with fastmcp python cli.py call-tool resend_command --command-id <value> --cancel-existing
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--command-id` | string | yes |  |
| `--cancel-existing` | boolean | no |  |

### test_elicitation

```bash
uv run --with fastmcp python cli.py call-tool test_elicitation
```

## Utility Commands

```bash
uv run --with fastmcp python cli.py list-tools
uv run --with fastmcp python cli.py list-resources
uv run --with fastmcp python cli.py read-resource <uri>
uv run --with fastmcp python cli.py list-prompts
uv run --with fastmcp python cli.py get-prompt <name> [key=value ...]
```
