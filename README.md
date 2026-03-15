# colab-cli

A CLI for running Python scripts and notebooks on Google Colab runtimes from your terminal. Designed for both human use and AI agent consumption (Claude Code, Codex).

## Quick Start

```bash
# One-time setup
colab login                              # Browser OAuth
colab connect --gpu t4                   # Allocate GPU runtime

# Run code
colab run script.py                      # Execute script, stream output
colab run -c "print('hello')"           # Inline code
colab run notebook.ipynb                 # Execute all notebook cells

# Files
colab push data.csv /content/data.csv    # Upload to runtime
colab pull /content/results.csv .        # Download from runtime

# Done
colab disconnect                         # Release runtime
```

## Setup

### 1. Install

```bash
# From source
git clone https://github.com/your-user/colab-cli.git
cd colab-cli
uv pip install -e .

# Or just run via uv without installing
uv run colab --help
```

Requires Python 3.11+.

### 2. Create a Google Cloud OAuth Client

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g., "colab-cli")
3. Navigate to **APIs & Services > OAuth consent screen**
   - User type: **External**
   - App name: anything (e.g., "colab-cli")
   - Add scopes: `email`, `profile`, and manually add `https://www.googleapis.com/auth/colaboratory`
   - Under **Test users**, add your Google email
4. Navigate to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Application type: **Desktop app**
   - Copy the **Client ID** and **Client Secret**

### 3. Configure the CLI

Create `~/.config/colab-cli/config.toml`:

```toml
[oauth]
client_id = "YOUR_CLIENT_ID.apps.googleusercontent.com"
client_secret = "YOUR_CLIENT_SECRET"
```

Or use environment variables:

```bash
export COLAB_CLIENT_ID="YOUR_CLIENT_ID.apps.googleusercontent.com"
export COLAB_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

### 4. Login

```bash
colab login
```

This opens your browser. You'll see an "unverified app" warning — click **Advanced > Go to colab-cli (unsafe)**. This is normal for personal GCP projects. Grant access and you're done. The refresh token is stored locally and auto-refreshed — you only need to login once.

## Commands

### Auth

```bash
colab login                    # Browser OAuth, stores refresh token
colab login --no-browser       # Manual code paste flow
colab logout                   # Clear stored tokens
colab whoami [--json]          # Show authenticated user
```

### Runtime

```bash
colab connect                  # Allocate CPU runtime
colab connect --gpu t4         # Request T4 GPU
colab connect --gpu v100       # Request V100 GPU
colab connect --gpu a100       # Request A100 GPU
colab status [--json]          # Show connection info, runtime health
colab disconnect               # Release runtime, stop keep-alive
```

The CLI sends keep-alive pings every 60s to prevent idle disconnection. The runtime stays alive as long as the CLI is running and you haven't hit Colab's time limit (~12h on free tier).

### Execute Code

```bash
colab run script.py [--json]             # Execute .py file
colab run notebook.ipynb [--json]        # Execute all cells sequentially
colab run -c "print('hello')" [--json]   # Inline code
```

Output streams to your terminal in real-time. Use `--json` for structured output (see below).

### Files

```bash
colab push local.csv /content/data.csv   # Upload file to runtime
colab pull /content/output.csv .         # Download file from runtime
colab ls [/content] [--json]             # List files on runtime
```

Binary files are handled automatically (base64 encoding).

## Agent Usage (Claude Code / Codex)

The CLI is designed as a tool for AI coding agents:

```bash
# Human sets up once:
colab login
colab connect --gpu t4

# Agent uses these commands:
colab status --json                      # Check runtime health
colab push model.py /content/model.py    # Upload code
colab run model.py --json                # Execute, parse results
colab run notebook.ipynb --json          # Test notebook
colab pull /content/output.csv .         # Get results
```

Key agent features:
- `--json` on all commands for machine-readable output
- Clean exit codes: 0=success, 1=execution error, 2=connection error, 3=runtime error
- Status messages go to stderr, output to stdout
- Non-interactive when not attached to a TTY

### JSON Output

`colab run --json` returns:

```json
{
  "status": "success",
  "exit_code": 0,
  "stdout": "Hello from Colab\n",
  "stderr": "",
  "error": null,
  "traceback": null,
  "duration_seconds": 0.37,
  "cells": [
    {
      "index": 0,
      "source": "print('Hello from Colab')",
      "status": "success",
      "stdout": "Hello from Colab\n",
      "stderr": "",
      "outputs": [],
      "error": null,
      "traceback": null
    }
  ]
}
```

`colab status --json` returns:

```json
{
  "connected": true,
  "endpoint": "m-s-abc123",
  "accelerator": "T4",
  "proxy_expires_at": "2026-03-15T20:00:00Z",
  "last_keepalive_at": "2026-03-15T19:05:00Z"
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Execution error (script/notebook raised an exception) |
| 2 | Auth or connection error |
| 3 | Runtime error (runtime died, timed out, reclaimed) |

## Development

```bash
# Run tests
uv run --extra dev pytest tests/unit

# Run with verbose output
uv run --extra dev pytest tests/unit -v

# Live smoke tests (requires configured OAuth + active Colab account)
COLAB_LIVE=1 uv run --extra dev pytest tests/live -m live
```

## Local State Files

| File | Purpose |
|------|---------|
| `~/.config/colab-cli/config.toml` | OAuth client ID + secret |
| `~/.config/colab-cli/token.json` | Refresh + access tokens |
| `~/.config/colab-cli/active.json` | Active runtime connection state |

## Limitations

- **Free tier**: ~12h runtime limit, GPU availability varies, Google may reclaim idle runtimes
- **No interactive shell** (yet): Phase 4 — `colab shell` for terminal access is planned
- **No Drive mount**: Colab's Drive mount requires a browser-based consent flow not yet supported
- **Rich output**: Images/HTML from notebooks are not rendered — text/plain fallback only
- **Undocumented API**: Built on Colab's internal API which may change without notice
