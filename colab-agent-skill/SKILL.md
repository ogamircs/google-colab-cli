# Colab CLI — Agent Skill

Use the `colab` CLI to execute Python code on a free Google Colab GPU runtime from the terminal. This is useful when you need GPU acceleration, a Linux environment, or access to Colab's pre-installed ML stack (PyTorch, TensorFlow, JAX, etc.).

## Prerequisites

Before running any commands, check authentication state:

```bash
colab auth status --json
```

If not authenticated, prompt the user to run `colab auth login`. Do **not** run the login command yourself — it requires interactive browser authentication.

## Workflow

### 1. Connect to a runtime

```bash
# CPU-only runtime
colab connect

# Request a specific GPU (t4, v100, a100)
colab connect --gpu t4
```

This allocates a Colab VM and keeps it alive in the background. You only need to connect once per session.

### 2. Check connection status

```bash
colab status
colab status --json   # machine-readable
```

### 3. Run code remotely

**Inline code:**

```bash
colab run -c "import torch; print(torch.cuda.is_available())"
```

**Run a Python script:**

```bash
colab run script.py
```

**Run a Jupyter notebook (executes all cells sequentially):**

```bash
colab run notebook.ipynb
```

**JSON output (for programmatic consumption):**

```bash
colab run -c "print('hello')" --json
```

The JSON output contains `status`, `exit_code`, `stdout`, `stderr`, `error`, `traceback`, `duration_seconds`, and per-cell results for notebooks.

### 4. Transfer files

**Push a local file to the runtime:**

```bash
colab push ./data.csv /content/data.csv
```

**Pull a file from the runtime:**

```bash
colab pull /content/results.csv ./results.csv
```

**List files on the runtime:**

```bash
colab ls              # list /content (default)
colab ls /content/drive
colab ls --json       # machine-readable
```

### 5. Disconnect

```bash
colab disconnect
```

## Tips for agents

- **Always check `colab auth status --json` first.** If not authenticated, ask the user to run `colab auth login`.
- **Always check status before running code.** If the runtime is not connected, connect first.
- **Use `--json` flags** on `run`, `status`, `ls`, and `auth whoami` when you need to parse output programmatically.
- **The runtime filesystem root is `/content`.** Pushed files and outputs live there.
- **Colab runtimes are ephemeral.** Files do not persist across disconnects. Push any results you need back to the local machine.
- **GPU availability is not guaranteed.** If `colab connect --gpu t4` succeeds, verify with `colab run -c "import torch; print(torch.cuda.get_device_name(0))"`.
- **Long-running tasks:** The CLI streams stdout/stderr in real time. The keepalive process runs in the background to prevent idle disconnection.
- **Exit codes propagate.** If remote code fails, `colab run` exits with a non-zero code.
- **Multi-step workflows:** Push data, run a script, pull results:
  ```bash
  colab push ./train.py /content/train.py
  colab push ./data.csv /content/data.csv
  colab run -c "exec(open('/content/train.py').read())"
  colab pull /content/model.pt ./model.pt
  ```

## Command reference

| Command | Purpose |
|---------|---------|
| `colab auth login [--no-browser]` | Authenticate with Google (interactive) |
| `colab auth logout` | Clear stored credentials |
| `colab auth whoami [--json]` | Show authenticated user |
| `colab auth status [--json]` | Check authentication state (agent-friendly, always exits 0) |
| `colab connect [--gpu TYPE]` | Start a Colab runtime |
| `colab status [--json]` | Check runtime connection state |
| `colab disconnect` | Shut down the runtime |
| `colab run TARGET [--json]` | Run a .py or .ipynb file remotely |
| `colab run -c CODE [--json]` | Run inline Python code remotely |
| `colab push LOCAL REMOTE` | Upload a file to the runtime |
| `colab pull REMOTE LOCAL` | Download a file from the runtime |
| `colab ls [PATH] [--json]` | List files on the runtime |
