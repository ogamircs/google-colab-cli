# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python CLI (`colab`) that runs local `.py`/`.ipynb` files on a Google Colab runtime from the terminal. Wraps Colab's **undocumented** tunnel/proxy APIs plus the runtime's Jupyter REST + WebSocket endpoints. Packaged for both human and agent use (JSON output, deterministic exit codes).

## Common commands

```bash
uv pip install -e .                                # editable install
uv run colab --help                                # run CLI without install

uv run --extra dev pytest tests/unit               # unit tests
uv run --extra dev pytest tests/unit/test_runtime.py::TestName   # single test
uv run --extra dev pytest tests/unit -v            # verbose

COLAB_LIVE=1 uv run --extra dev pytest tests/live -m live        # live smoke (needs OAuth + Colab account)
```

`live` pytest marker gates smoke tests — unit tests skip them by default (see `tool.pytest.ini_options` in `pyproject.toml`). `pythonpath = ["src"]` means tests import `colab_cli` without install.

Entry point: `colab = "colab_cli.cli:main"` (`pyproject.toml`). CLI also runnable as `python -m colab_cli.cli`.

## Architecture

Layered. CLI → `RuntimeManager` → three protocol clients. All async via `httpx` / `websockets`; Typer commands wrap with `asyncio.run`.

```
src/colab_cli/
  cli/              Typer commands — one file per command group (auth, connect, run, files)
  core/
    runtime.py      RuntimeManager — orchestrates connect/run/push/pull/keepalive. Single entry via create_runtime_manager().
    connection.py   ConnectionStore — persists ActiveConnection to ~/.config/colab-cli/active.json (chmod 0600).
    secrets.py      Builds Python shim injecting google.colab.userdata.get() on remote.
    auth/           OAuth (desktop flow), token refresh via google-auth, token_store (chmod 0600 token.json).
    colab/client.py ColabClient — Colab's tunnel/proxy API (assign, unassign, keep-alive, runtime-proxy-token).
    jupyter/rest.py JupyterRestClient — sessions + file contents over the runtime proxy.
    jupyter/ws.py   KernelWebSocketClient — executes code cells, accumulates stream/display_data/execute_reply.
  formats/          Notebook cell extraction + JSON/human output formatting.
  models.py         All Pydantic models. AppConfig/OAuthConfig/CellResult/RunResult/ActiveConnection.
  config.py paths.py errors.py utils.py
```

### Request flow for `colab run`

1. `cli/run.py` → `RuntimeManager.run_code/run_script/run_notebook`
2. `_ensure_connection` loads `active.json`; refreshes proxy token via `ColabClient.fetch_runtime_proxy_token` if <5 min to expiry
3. `_ensure_session` lazily creates Jupyter session + kernel on first run per connection, caches `session_id`/`kernel_id` back into `active.json`
4. If secrets provided → `_inject_secrets` executes `build_secrets_setup_code(...)` cell to monkey-patch `google.colab.userdata` in the remote kernel
5. `KernelWebSocketClient.execute` opens WSS per cell, sends Jupyter `execute_request`, drains messages until `execute_reply` + `status:idle` with matching `parent_header.msg_id`
6. Cells map to `CellResult`; aggregated into `RunResult`

### Connect flow

`ColabClient.assign_runtime` does a two-step XSRF dance: GET `/tun/m/assign` to fetch a handshake token, then POST the same URL with `x-xsrf-protected` header. The XSSI guard prefix `)]}'\n` is stripped in `utils.strip_xssi_prefix`.

### Keep-alive

`connect` spawns a detached subprocess `python -m colab_cli.cli _internal_keepalive` (hidden Typer command) that loops `RuntimeManager.keepalive_once` every 60s and refreshes the proxy token when near expiry. PID is stored in `active.json`; `disconnect` SIGTERMs it.

## Exit codes

Mapped centrally in `cli/__init__.py:main` via exception hierarchy in `errors.py`:

| Exception           | Exit |
|---------------------|------|
| `ExecutionError`    | 1    |
| `AuthError`, `ConnectionError` | 2 |
| `ColabRuntimeError` | 3    |
| `ColabCliError` (base) | 1 |

`run` additionally exits with `result.exit_code` (1 if remote code raised). Preserve this mapping when adding commands.

## Local state

All under `~/.config/colab-cli/` (path helpers in `paths.py`):

- `config.toml` — OAuth client_id/secret. Env vars `COLAB_CLIENT_ID`/`COLAB_CLIENT_SECRET`/`COLAB_DEFAULT_ACCELERATOR`/`COLAB_AUTHUSER` override file values.
- `token.json` — access + refresh tokens (chmod 0600).
- `active.json` — `ActiveConnection` Pydantic model (chmod 0600): endpoint_id, proxy_url/token/expiry, session_id, kernel_id, keepalive_pid.

`StrictModel` (Pydantic `extra="forbid"`) is used for internal/config models; Colab API response models allow extras since the API is undocumented and adds fields.

## Conventions

- **stderr vs stdout**: status/progress messages → stderr (`typer.echo(..., err=True)`); user-facing output → stdout. `--json` writes a single JSON object to stdout so agents can parse.
- **`--json` flag** on `run`, `status`, `ls`, `auth whoami`, `auth status`. When adding output-producing commands, support both paths via `format_json` and a human formatter in `formats/output.py`.
- **`allow_missing_config=True`** is used by `status`/`disconnect` so they work without OAuth creds configured. Reuse this for any read-only command that inspects local state.
- **Secrets**: `--secret KEY=VALUE` (repeatable) and `--secrets-file path` merge via `_collect_secrets`; file is `.env`-style. The generated setup cell patches `google.colab.userdata.get` — do NOT log `secrets` or include in `RunResult`.
- **Tests mock the network**: `test_runtime.py` injects fake `colab_client_factory` / `jupyter_rest_factory` / `kernel_client_factory` into `RuntimeManager`. Preserve this DI when extending `RuntimeManager`.
- **Async all the way** in `core/`; only `cli/` bridges with `asyncio.run`.

## Caveats

- Colab tunnel/proxy APIs are **undocumented** and can change without notice. Tests guard against response-shape drift (Pydantic `extra="allow"` on `Assigned*`, `RuntimeProxy*`, `Jupyter*` models).
- WS ping is disabled (`ping_interval=None`) because the Colab proxy doesn't reliably forward pings — don't re-enable.
- Rich/binary notebook outputs (images, HTML) are not rendered; only `text/plain` fallback is emitted. `decode_contents_payload` handles base64 binary files for push/pull.
- Free-tier Colab ~12h limit; runtime may be reclaimed — surface as `ColabRuntimeError` (exit 3) when detected.

## Python API (`src/colab_cli/api/`)

Sibling surface to the CLI: run Colab code from inside Python. `colab(gpu="t4")` returns a `ColabSession` (context manager); auto-connects if `active.json` is empty (owning the runtime, disconnect on exit) or attaches silently to a CLI-owned runtime (never touches its lifecycle on exit). `@remote(gpu="t4")` decorator runs a function on Colab via `cloudpickle` push/pull of args+return; remote exceptions re-raise locally as `RemoteExecutionError` with the original chained as `__cause__`.

`api/_sync.py::SyncRunner` runs a private event loop on a daemon thread and dispatches via `asyncio.run_coroutine_threadsafe`, so the sync API works inside Jupyter (running loop) and async callers alike. Singleton via `get_runner()`; `reset_runner()` for tests + post-fork. All `RuntimeManager` methods and `ConnectionStore`/`active.json` are reused — the API is a thin sync facade, not a parallel implementation.

Decorator harness (`api/_harness.py`) executes inside the kernel, loads pickled fn+args from `/content/.colab_cli/<slug>/`, writes `("ok", value)` or `("err", exc, tb_list)` back, prints the `__COLAB_CLI_DONE__:<path>` marker. Local side pulls the envelope, unpickles, returns or raises.

## Adding commands

1. New file in `cli/` exporting `register(app: typer.Typer)`; wire in `cli/__init__.py`.
2. Obtain runtime via `create_runtime_manager(spawn_keepalive=False, allow_missing_config=<read-only?>)`.
3. Raise one of `errors.py` exceptions — `main()` handles exit mapping.
4. Add a unit test file under `tests/unit/` using DI factories; avoid real network.
