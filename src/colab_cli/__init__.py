"""colab-cli package."""

from __future__ import annotations

__version__ = "0.1.0"


def __getattr__(name: str):
    # Lazy re-exports so importing colab_cli does not pull in cloudpickle /
    # runtime code paths unless the Python API is actually used.
    if name in {"colab", "remote", "ColabSession", "RemoteExecutionError"}:
        from . import api

        return getattr(api, name)
    raise AttributeError(f"module 'colab_cli' has no attribute {name!r}")


__all__ = [
    "ColabSession",
    "RemoteExecutionError",
    "__version__",
    "colab",
    "remote",
]

