"""Dispatch provider registry and factory."""

from __future__ import annotations

from typing import Any

from ..config import DispatcherConfig
from .base import DispatchProvider


def _coerce_dispatcher_config(config: Any) -> DispatcherConfig:
    if isinstance(config, DispatcherConfig):
        return config
    return DispatcherConfig(
        provider=getattr(config, "provider", "claude_cli"),
        timeout_seconds=getattr(config, "timeout_seconds", 300),
        stall_timeout_seconds=getattr(
            config,
            "stall_timeout",
            getattr(config, "stall_timeout_seconds", 600),
        ),
        permission_mode=getattr(config, "permission_mode", "bypassPermissions"),
        max_output_chars=getattr(config, "max_output_chars", 50_000),
    )


def create_provider(config: Any) -> DispatchProvider:
    """Create a dispatch provider from config.

    Args:
        config: Dispatcher configuration with provider field.

    Returns:
        A DispatchProvider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    dispatcher_config = _coerce_dispatcher_config(config)
    provider_name = dispatcher_config.provider

    if provider_name == "claude_cli":
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(dispatcher_config)

    if provider_name == "codex_cli":
        from .codex_cli import CodexCLIProvider

        return CodexCLIProvider(dispatcher_config)

    raise ValueError(f"Unknown provider: {provider_name!r}. Available: ['claude_cli', 'codex_cli']")
