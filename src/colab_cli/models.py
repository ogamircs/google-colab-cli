"""Shared Pydantic models for configuration and runtime state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OAuthConfig(StrictModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    auth_uri: HttpUrl = Field(default="https://accounts.google.com/o/oauth2/auth")
    token_uri: HttpUrl = Field(default="https://oauth2.googleapis.com/token")
    scopes: tuple[str, ...] = (
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/colaboratory",
    )


class AppConfig(StrictModel):
    oauth: OAuthConfig
    default_accelerator: str | None = None
    default_authuser: int = 0


class TokenData(StrictModel):
    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str = ""
    token_type: str = "Bearer"
    issued_at: datetime | None = None


class ProxyInfo(StrictModel):
    url: str = Field(min_length=1)
    token: str = Field(min_length=1)
    expires_at: datetime


class ActiveConnection(StrictModel):
    notebook_hash: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    proxy_url: str = Field(min_length=1)
    proxy_token: str = Field(min_length=1)
    proxy_expires_at: datetime
    accelerator: str | None = None
    authuser: int = 0
    last_keepalive_at: datetime | None = None
    keepalive_pid: int | None = None
    session_id: str | None = None
    kernel_id: str | None = None

    @model_validator(mode="after")
    def validate_proxy_state(self) -> "ActiveConnection":
        if not self.proxy_expires_at:
            raise ValueError("proxy_expires_at is required")
        return self


class StatusResult(StrictModel):
    connected: bool
    endpoint: str | None = None
    accelerator: str | None = None
    proxy_expires_at: datetime | None = None
    last_keepalive_at: datetime | None = None
    notebook_hash: str | None = None


class CellResult(StrictModel):
    index: int
    source: str
    status: Literal["success", "error"]
    stdout: str = ""
    stderr: str = ""
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    traceback: list[str] | None = None


class RunResult(StrictModel):
    status: Literal["success", "error"]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    traceback: list[str] | None = None
    duration_seconds: float = 0.0
    cells: list[CellResult] = Field(default_factory=list)


class AssignHandshake(BaseModel):
    """GET /tun/m/assign response — allow extra fields from Colab API."""
    token: str = Field(min_length=1)
    variant: str | None = None
    accelerator: str | None = Field(default=None, alias="acc")
    notebook_hash: str | None = Field(default=None, alias="nbh")
    project: bool | str | None = Field(default=None, alias="p")


class RuntimeProxyTokenResponse(BaseModel):
    """GET /v1/runtime-proxy-token response — allow extra fields from Colab API."""
    token: str = Field(min_length=1)
    url: str = Field(min_length=1)
    token_ttl: str | None = Field(default=None, alias="tokenTtl")

    @field_validator("token_ttl", mode="before")
    @classmethod
    def normalize_ttl(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return str(value)


class RuntimeProxyInfo(BaseModel):
    """Runtime proxy info nested in assign response — allow extra fields."""
    url: str = Field(min_length=1)
    token: str = Field(min_length=1)
    token_expires_in_seconds: int | None = Field(default=None, alias="tokenExpiresInSeconds")


class AssignedRuntime(BaseModel):
    """POST /tun/m/assign response — allow extra fields from Colab API."""
    endpoint: str = Field(min_length=1)
    accelerator: str | None = None
    runtime_proxy_info: RuntimeProxyInfo | None = Field(default=None, alias="runtimeProxyInfo")


class UserInfo(BaseModel):
    """Google userinfo response — allow extra fields (given_name, family_name, etc.)."""
    sub: str | None = None
    email: str | None = None
    name: str | None = None
    picture: str | None = None


class JupyterSessionKernel(BaseModel):
    id: str
    name: str


class JupyterSession(BaseModel):
    """Jupyter session response — allow extra fields."""
    id: str
    path: str
    name: str | None = None
    type: str | None = None
    kernel: JupyterSessionKernel


class JupyterContent(BaseModel):
    """Jupyter contents response — allow extra fields."""
    name: str
    path: str
    type: str
    format: str | None = None
    content: Any = None
    mimetype: str | None = None

