"""Tests for the dispatch provider abstraction."""

import pytest
from unittest.mock import patch

from lindy_orchestrator.config import DispatcherConfig
from lindy_orchestrator.providers import create_provider
from lindy_orchestrator.providers.base import DispatchProvider
from lindy_orchestrator.providers.claude_cli import ClaudeCLIProvider


class TestDispatchProviderProtocol:
    def test_claude_cli_satisfies_protocol(self):
        config = DispatcherConfig()
        provider = ClaudeCLIProvider(config)
        assert isinstance(provider, DispatchProvider)

    def test_protocol_has_dispatch_method(self):
        config = DispatcherConfig()
        provider = ClaudeCLIProvider(config)
        assert hasattr(provider, "dispatch")
        assert callable(provider.dispatch)

    def test_protocol_has_dispatch_simple_method(self):
        config = DispatcherConfig()
        provider = ClaudeCLIProvider(config)
        assert hasattr(provider, "dispatch_simple")
        assert callable(provider.dispatch_simple)


class TestCreateProvider:
    def test_default_provider(self):
        config = DispatcherConfig()
        provider = create_provider(config)
        assert isinstance(provider, ClaudeCLIProvider)

    def test_explicit_claude_cli(self):
        config = DispatcherConfig(provider="claude_cli")
        provider = create_provider(config)
        assert isinstance(provider, ClaudeCLIProvider)

    def test_unknown_provider_raises(self):
        config = DispatcherConfig(provider="unknown")
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider(config)

    def test_provider_stores_config(self):
        config = DispatcherConfig(timeout_seconds=42)
        provider = create_provider(config)
        assert isinstance(provider, ClaudeCLIProvider)
        assert provider.config.timeout_seconds == 42


class TestClaudeCLIProvider:
    def test_init(self):
        config = DispatcherConfig(
            timeout_seconds=100,
            permission_mode="plan",
        )
        provider = ClaudeCLIProvider(config)
        assert provider.config.timeout_seconds == 100
        assert provider.config.permission_mode == "plan"

    def test_default_provider_field(self):
        config = DispatcherConfig()
        assert config.provider == "claude_cli"


class TestDispatcherConfigProvider:
    def test_provider_field_default(self):
        config = DispatcherConfig()
        assert config.provider == "claude_cli"

    def test_provider_field_custom(self):
        config = DispatcherConfig(provider="custom_provider")
        assert config.provider == "custom_provider"

    def test_backward_compatible_fields(self):
        """Existing fields still work alongside new provider field."""
        config = DispatcherConfig(
            provider="claude_cli",
            timeout_seconds=1800,
            permission_mode="bypassPermissions",
            max_output_chars=50_000,
        )
        assert config.provider == "claude_cli"
        assert config.timeout_seconds == 1800


class TestProviderCallChain:
    """Verify that provider.dispatch() actually calls the underlying dispatcher functions."""

    @patch("lindy_orchestrator.providers.claude_cli.dispatch_agent")
    def test_dispatch_calls_dispatch_agent(self, mock_dispatch):
        from pathlib import Path
        from lindy_orchestrator.models import DispatchResult

        mock_dispatch.return_value = DispatchResult(
            module="backend",
            success=True,
            output="done",
            exit_code=0,
            duration_seconds=1.0,
            event_count=5,
        )
        config = DispatcherConfig()
        provider = create_provider(config)
        result = provider.dispatch(
            module="backend",
            working_dir=Path("/tmp"),
            prompt="test prompt",
        )
        assert result.success
        assert mock_dispatch.called
        args = mock_dispatch.call_args
        assert args[0][0] == "backend"  # module
        assert args[0][2] == "test prompt"  # prompt

    @patch("lindy_orchestrator.providers.claude_cli.dispatch_agent_simple")
    def test_dispatch_simple_calls_dispatch_agent_simple(self, mock_simple):
        from pathlib import Path
        from lindy_orchestrator.models import DispatchResult

        mock_simple.return_value = DispatchResult(
            module="planner",
            success=True,
            output="plan",
            exit_code=0,
        )
        config = DispatcherConfig()
        provider = create_provider(config)
        result = provider.dispatch_simple(
            module="planner",
            working_dir=Path("/tmp"),
            prompt="plan prompt",
        )
        assert result.success
        assert mock_simple.called

    @patch("lindy_orchestrator.providers.claude_cli.dispatch_agent")
    def test_on_event_callback_passed_through(self, mock_dispatch):
        from pathlib import Path
        from lindy_orchestrator.models import DispatchResult

        mock_dispatch.return_value = DispatchResult(
            module="backend",
            success=True,
            output="ok",
            exit_code=0,
        )

        def callback(event):
            pass

        config = DispatcherConfig()
        provider = create_provider(config)
        provider.dispatch(
            module="backend",
            working_dir=Path("/tmp"),
            prompt="test",
            on_event=callback,
        )
        args = mock_dispatch.call_args
        assert args[0][4] is callback  # on_event is the 5th positional arg
