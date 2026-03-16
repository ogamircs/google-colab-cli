"""Human and JSON output helpers."""

from __future__ import annotations

import json

from colab_cli.models import RunResult, StatusResult


def format_json(model: RunResult | StatusResult | dict[str, object]) -> str:
    if hasattr(model, "model_dump"):
        return json.dumps(model.model_dump(mode="json"), indent=2)
    return json.dumps(model, indent=2)


def format_human_status(status: StatusResult) -> str:
    if not status.connected:
        return "Disconnected"
    parts = [f"Connected to {status.endpoint}"]
    if status.accelerator:
        parts.append(f"accelerator={status.accelerator}")
    if status.proxy_expires_at:
        parts.append(f"proxy_expires_at={status.proxy_expires_at.isoformat()}")
    return " | ".join(parts)


def format_human_run(result: RunResult) -> str:
    if result.stderr:
        return f"{result.stdout}{result.stderr}"
    return result.stdout
