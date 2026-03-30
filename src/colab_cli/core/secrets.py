"""Secrets parsing and injection for google.colab.userdata support."""

from __future__ import annotations

from pathlib import Path

from colab_cli.errors import ConfigError


def parse_key_value(raw: str) -> tuple[str, str]:
    """Parse a KEY=VALUE string, stripping whitespace and optional surrounding quotes."""
    if "=" not in raw:
        raise ConfigError(f"Invalid secret format: {raw!r} — expected KEY=VALUE")
    key, value = raw.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def parse_secrets_file(path: Path) -> dict[str, str]:
    """Parse a .env-style secrets file into a dict of key-value pairs."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError(f"Secrets file not found: {path}")

    secrets: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            key, value = parse_key_value(line)
        except ConfigError:
            raise ConfigError(f"Invalid secret at {path}:{lineno} — expected KEY=VALUE")
        secrets[key] = value
    return secrets


def build_secrets_setup_code(secrets: dict[str, str]) -> str:
    """Generate Python code that patches google.colab.userdata.get() on the runtime."""
    dict_literal = "{" + ", ".join(f"{k!r}: {v!r}" for k, v in secrets.items()) + "}"
    return (
        "import types as _types, sys as _sys\n"
        f"_secrets = {dict_literal}\n"
        '_mod = _sys.modules.get("google.colab.userdata")\n'
        "if _mod is None:\n"
        '    _goog = _sys.modules.setdefault("google", _types.ModuleType("google"))\n'
        '    _goog.__path__ = getattr(_goog, "__path__", [])\n'
        '    _colab = _sys.modules.setdefault("google.colab", _types.ModuleType("google.colab"))\n'
        '    _colab.__path__ = getattr(_colab, "__path__", [])\n'
        '    _mod = _types.ModuleType("google.colab.userdata")\n'
        '    _sys.modules["google.colab.userdata"] = _mod\n'
        "    _goog.colab = _colab\n"
        "    _colab.userdata = _mod\n"
        "def _get(key, _s=_secrets):\n"
        "    if key not in _s:\n"
        "        raise KeyError(f\"Secret '{key}' not found\")\n"
        "    return _s[key]\n"
        "_mod.get = _get\n"
        "del _types, _sys, _secrets, _mod, _get\n"
    )
