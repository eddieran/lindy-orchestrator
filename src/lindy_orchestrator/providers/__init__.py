"""Dispatch provider registry and factory."""

from __future__ import annotations

from ..config import DispatcherConfig
from .base import DispatchProvider


def create_provider(config: DispatcherConfig) -> DispatchProvider:
    """Create a dispatch provider from config.

    Args:
        config: Dispatcher configuration with provider field.

    Returns:
        A DispatchProvider instance.

    Raises:
        ValueError: If the provider name is unknown.
    """
    provider_name = config.provider

    if provider_name == "claude_cli":
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(config)

    raise ValueError(f"Unknown provider: {provider_name!r}. Available: ['claude_cli']")
