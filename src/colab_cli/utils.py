"""Common helpers used across the colab-cli package."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

XSSI_PREFIX = ")]}'\n"


def utc_now() -> datetime:
    return datetime.now(UTC)


def strip_xssi_prefix(payload: str) -> str:
    if payload.startswith(XSSI_PREFIX):
        return payload[len(XSSI_PREFIX) :]
    return payload


def generate_notebook_hash() -> str:
    raw = str(uuid.uuid4()).replace("-", "_")
    return raw.ljust(44, ".")


def should_refresh_soon(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
    threshold: timedelta = timedelta(minutes=5),
) -> bool:
    if expires_at is None:
        return True
    current = now or utc_now()
    return expires_at <= current + threshold


def ttl_to_expiry(ttl_seconds: int | float, *, now: datetime | None = None) -> datetime:
    current = now or utc_now()
    return current + timedelta(seconds=float(ttl_seconds))
