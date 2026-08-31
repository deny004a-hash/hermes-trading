"""Shared adapter schema contract."""

from __future__ import annotations

from typing import Any, Mapping

SCHEMA_VERSION = 1


class SchemaError(RuntimeError):
    """Raised when an upstream payload no longer matches our contract."""


def require_schema(payload: Mapping[str, Any], expected: int = SCHEMA_VERSION) -> None:
    actual = payload.get("schema_version")
    if actual != expected:
        raise SchemaError(f"schema_version mismatch: expected {expected}, got {actual!r}")
