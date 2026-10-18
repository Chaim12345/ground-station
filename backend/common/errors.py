"""Compact, device-specific diagnostics for SDR probe and worker errors."""

from typing import Any


def format_device_error(backend: str, stage: str, error: Exception, **context: Any) -> str:
    """Keep the native message and identify the device and operation that failed."""
    details = ", ".join(
        f"{key}={value}" for key, value in context.items() if value not in (None, "", 0)
    )
    location = f" ({details})" if details else ""
    native_message = str(error).strip()
    cause = f"{type(error).__name__}: {native_message}" if native_message else type(error).__name__
    return f"{backend} {stage} failed{location}: {cause}"
