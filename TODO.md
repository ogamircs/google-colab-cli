# TODO

Improvement backlog from a full codebase review (2026-07-03). Ordered by priority within each section.
Checked items were implemented on `fix/review-hardening`.

## Correctness

- [x] **Fix `active.json` lost-update race between keepalive subprocess and main process.**
  ConnectionStore now writes via temp file + `os.replace` and exposes a flock-guarded `update()`
  used for keepalive heartbeats, proxy refreshes, and session ids — concurrent writers can no
  longer clobber each other's fields.

- [x] **Implement the `ColabRuntimeError` (exit 3) contract — it was documented but never raised.**
  All three protocol clients now map failures onto the exception hierarchy: 401/403 → `AuthError`,
  404/410 on runtime-scoped endpoints and WS handshake failures / mid-run disconnects →
  `ColabRuntimeError`, transport errors → `ConnectionError`. Contents 404s name the missing path.
  - [ ] Follow-up: on a detected dead kernel (stale `kernel_id` after runtime restart), recreate
    the session automatically instead of asking the user to reconnect.

- [x] **Honor mapped exit codes at the real CLI entry point.** `main()` raised `typer.Exit`
  outside `app()`, so every mapped error leaked a traceback and exited 1 instead of its
  documented code. (Found by end-to-end smoke testing this branch.)

- [x] **Bump `websockets` lower bound to `>=14.0`.** `additional_headers` doesn't exist in 12/13.

- [x] **Rework execution timeouts.** The hardcoded 300s per-message-silence timeout is now an
  explicit idle timeout defaulting to None (wait indefinitely), exposed as `colab run --timeout`,
  mapped to `ExecutionError`, and routed consistently through the Python API
  (`ColabSession.run` / `NotebookHandle.run`).

- [x] **Close HTTP clients in `finally` on error paths.** Extracted a `_jupyter_client()` async
  context manager; removed the 4× duplicated client construction.

- [x] **Harden keepalive PID handling.** The stored PID's command line is verified against the
  `_internal_keepalive` marker before SIGTERM; `status` now reports `keepalive_running`.

## Engineering hygiene

- [x] **Add CI.** `.github/workflows/ci.yml` runs unit tests on Python 3.11–3.13 plus ruff + mypy.
- [x] **Add ruff + mypy** config in `pyproject.toml`; both pass clean.
- [x] **Ship a `py.typed` marker.**
- [x] **Packaging nits:** dropped unused direct `rich` dependency, fixed `authors`,
  gitignored `.DS_Store` and `dist/`.

## Design polish

- [ ] **Stop mutating private state from the API layer:** `api/session.py` sets
  `mgr._spawn_keepalive` — add a public parameter or setter on `RuntimeManager`.
- [ ] **Pool HTTP/WS connections for API sessions.** Every operation builds a new
  `httpx.AsyncClient` (fresh TLS handshake) and every cell opens a fresh WSS connection; a
  `ColabSession` doing many `run()` calls should reuse a client owned by the `RuntimeManager`.
- [ ] **Make token refresh non-blocking:** `credentials.get_valid_token()` does a synchronous
  google-auth refresh inside async methods, stalling the event loop (matters for the shared
  `SyncRunner` loop thread).
- [ ] **Reduce `@remote` cold-start cost:** without a session it allocates and tears down a whole
  runtime per call. Consider an attach-or-connect keep-warm default with explicit opt-out.
