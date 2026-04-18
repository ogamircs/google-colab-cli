"""Python-level API for running code on Colab runtimes from inside Python."""

from __future__ import annotations

from colab_cli.errors import RemoteExecutionError

from .decorator import remote
from .session import ColabSession, colab

__all__ = ["ColabSession", "RemoteExecutionError", "colab", "remote"]
