"""Custom exceptions for colab-cli."""

from __future__ import annotations


class ColabCliError(Exception):
    """Base exception for colab-cli."""


class ConfigError(ColabCliError):
    """Raised when configuration is missing or invalid."""


class AuthError(ColabCliError):
    """Raised for authentication failures."""


class ConnectionError(ColabCliError):
    """Raised for connection lifecycle failures."""


class ExecutionError(ColabCliError):
    """Raised for remote execution failures."""


class ColabRuntimeError(ColabCliError):
    """Raised when a remote runtime becomes unusable."""


class RemoteExecutionError(ExecutionError):
    """Raised when user code fails on the remote runtime (from the Python API)."""

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        remote_traceback: list[str] | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.error = error
        self.remote_traceback = list(remote_traceback or [])
        self.stdout = stdout
        self.stderr = stderr

