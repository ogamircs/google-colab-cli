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

