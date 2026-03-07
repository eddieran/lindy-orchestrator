"""Tests for the Codex CLI dispatch provider abstraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from lindy_orchestrator.config import DispatcherConfig
from lindy_orchestrator.models import DispatchResult
from lindy_orchestrator.providers import create_provider
from lindy_orchestrator.providers.base import DispatchProvider
from lindy_orchestrator.providers.codex_cli import CodexCLIProvider


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestCodexCLIProviderProtocol:
    def test_satisfies_dispatch_provider(self):
        config = DispatcherConfig()
        provider = CodexCLIProvider(config)
        assert isinstance(provider, DispatchProvider)

    def test_has_dispatch_method(self):
        config = DispatcherConfig()
        provider = CodexCLIProvider(config)
        assert hasattr(provider, "dispatch")
        assert callable(provider.dispatch)

    def test_has_dispatch_simple_method(self):
        config = DispatcherConfig()
        provider = CodexCLIProvider(config)
        assert hasattr(provider, "dispatch_simple")
        assert callable(provider.dispatch_simple)

    def test_dispatch_signature_matches_protocol(self):
        """Verify dispatch accepts the same parameters as the Protocol."""
        import inspect

        sig = inspect.signature(CodexCLIProvider.dispatch)
        params = list(sig.parameters.keys())
        assert "module" in params
        assert "working_dir" in params
        assert "prompt" in params
        assert "on_event" in params
        assert "stall_seconds" in params

    def test_dispatch_simple_signature_matches_protocol(self):
        import inspect

        sig = inspect.signature(CodexCLIProvider.dispatch_simple)
        params = list(sig.parameters.keys())
        assert "module" in params
        assert "working_dir" in params
        assert "prompt" in params


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


class TestCreateProviderCodex:
    def test_codex_cli_provider(self):
        config = DispatcherConfig(provider="codex_cli")
        provider = create_provider(config)
        assert isinstance(provider, CodexCLIProvider)

    def test_codex_provider_stores_config(self):
        config = DispatcherConfig(provider="codex_cli", timeout_seconds=99)
        provider = create_provider(config)
        assert isinstance(provider, CodexCLIProvider)
        assert provider.config.timeout_seconds == 99

    def test_unknown_provider_error_lists_codex(self):
        config = DispatcherConfig(provider="nonexistent")
        with pytest.raises(ValueError, match="codex_cli"):
            create_provider(config)

    def test_default_still_claude(self):
        """Default provider remains claude_cli, not codex_cli."""
        config = DispatcherConfig()
        provider = create_provider(config)
        assert type(provider).__name__ == "ClaudeCLIProvider"

    def test_codex_provider_isinstance_dispatch_provider(self):
        config = DispatcherConfig(provider="codex_cli")
        provider = create_provider(config)
        assert isinstance(provider, DispatchProvider)


# ---------------------------------------------------------------------------
# CodexCLIProvider init & config
# ---------------------------------------------------------------------------


class TestCodexCLIProviderInit:
    def test_init_stores_config(self):
        config = DispatcherConfig(
            timeout_seconds=100,
            stall_timeout_seconds=50,
            permission_mode="plan",
        )
        provider = CodexCLIProvider(config)
        assert provider.config.timeout_seconds == 100
        assert provider.config.stall_timeout_seconds == 50
        assert provider.config.permission_mode == "plan"

    def test_init_with_codex_provider_name(self):
        config = DispatcherConfig(provider="codex_cli")
        provider = CodexCLIProvider(config)
        assert provider.config.provider == "codex_cli"


# ---------------------------------------------------------------------------
# Provider call chain
# ---------------------------------------------------------------------------


class TestCodexProviderCallChain:
    @patch("lindy_orchestrator.providers.codex_cli.dispatch_codex_agent")
    def test_dispatch_calls_dispatch_codex_agent(self, mock_dispatch):
        mock_dispatch.return_value = DispatchResult(
            module="backend",
            success=True,
            output="done",
            exit_code=0,
            duration_seconds=1.0,
            event_count=5,
        )
        config = DispatcherConfig(provider="codex_cli")
        provider = CodexCLIProvider(config)
        result = provider.dispatch(
            module="backend",
            working_dir=Path("/tmp"),
            prompt="test prompt",
        )
        assert result.success
        assert mock_dispatch.called
        args = mock_dispatch.call_args
        assert args[0][0] == "backend"
        assert args[0][2] == "test prompt"

    @patch("lindy_orchestrator.providers.codex_cli.dispatch_codex_agent_simple")
    def test_dispatch_simple_calls_dispatch_codex_agent_simple(self, mock_simple):
        mock_simple.return_value = DispatchResult(
            module="planner",
            success=True,
            output="plan",
            exit_code=0,
        )
        config = DispatcherConfig(provider="codex_cli")
        provider = CodexCLIProvider(config)
        result = provider.dispatch_simple(
            module="planner",
            working_dir=Path("/tmp"),
            prompt="plan prompt",
        )
        assert result.success
        assert mock_simple.called

    @patch("lindy_orchestrator.providers.codex_cli.dispatch_codex_agent")
    def test_on_event_callback_passed_through(self, mock_dispatch):
        mock_dispatch.return_value = DispatchResult(
            module="backend",
            success=True,
            output="ok",
            exit_code=0,
        )

        def callback(event: dict[str, Any]) -> None:
            pass

        config = DispatcherConfig(provider="codex_cli")
        provider = CodexCLIProvider(config)
        provider.dispatch(
            module="backend",
            working_dir=Path("/tmp"),
            prompt="test",
            on_event=callback,
        )
        args = mock_dispatch.call_args
        assert args[0][4] is callback

    @patch("lindy_orchestrator.providers.codex_cli.dispatch_codex_agent")
    def test_stall_seconds_passed_through(self, mock_dispatch):
        mock_dispatch.return_value = DispatchResult(
            module="backend",
            success=True,
            output="ok",
            exit_code=0,
        )
        config = DispatcherConfig(provider="codex_cli")
        provider = CodexCLIProvider(config)
        provider.dispatch(
            module="backend",
            working_dir=Path("/tmp"),
            prompt="test",
            stall_seconds=120,
        )
        kwargs = mock_dispatch.call_args[1]
        assert kwargs["stall_seconds"] == 120


# ---------------------------------------------------------------------------
# Config provider field validation
# ---------------------------------------------------------------------------


class TestDispatcherConfigCodexProvider:
    def test_codex_cli_as_provider(self):
        config = DispatcherConfig(provider="codex_cli")
        assert config.provider == "codex_cli"

    def test_backward_compatible_with_codex(self):
        config = DispatcherConfig(
            provider="codex_cli",
            timeout_seconds=900,
            stall_timeout_seconds=300,
            permission_mode="bypassPermissions",
            max_output_chars=100_000,
        )
        assert config.provider == "codex_cli"
        assert config.timeout_seconds == 900
        assert config.max_output_chars == 100_000
