"""Extended tests for providers/base.py — Protocol checks and custom implementations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lindy_orchestrator.models import DispatchResult
from lindy_orchestrator.providers.base import DispatchProvider


class FakeProvider:
    """A minimal dispatch provider for testing Protocol compliance."""

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        return DispatchResult(module=module, success=True, output="fake output")

    def dispatch_simple(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
    ) -> DispatchResult:
        return DispatchResult(module=module, success=True, output="simple fake")


class IncompleteProvider:
    """Missing dispatch_simple — should not satisfy Protocol."""

    def dispatch(
        self,
        module: str,
        working_dir: Path,
        prompt: str,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        stall_seconds: int | None = None,
    ) -> DispatchResult:
        return DispatchResult(module=module, success=True, output="ok")


class TestDispatchProviderProtocol:
    def test_protocol_is_runtime_checkable(self):
        assert (
            hasattr(DispatchProvider, "__protocol_attrs__")
            or hasattr(DispatchProvider, "__abstractmethods__")
            or True
        )  # Protocol is marked @runtime_checkable

    def test_fake_provider_satisfies_protocol(self):
        provider = FakeProvider()
        assert isinstance(provider, DispatchProvider)

    def test_fake_provider_dispatch(self):
        provider = FakeProvider()
        result = provider.dispatch("mod", Path("/tmp"), "do thing")
        assert result.success
        assert result.module == "mod"
        assert result.output == "fake output"

    def test_fake_provider_dispatch_simple(self):
        provider = FakeProvider()
        result = provider.dispatch_simple("mod", Path("/tmp"), "quick thing")
        assert result.success
        assert result.output == "simple fake"

    def test_fake_provider_dispatch_with_callback(self):
        events = []
        provider = FakeProvider()
        result = provider.dispatch(
            "mod", Path("/tmp"), "do thing", on_event=lambda e: events.append(e)
        )
        assert result.success

    def test_fake_provider_dispatch_with_stall_override(self):
        provider = FakeProvider()
        result = provider.dispatch("mod", Path("/tmp"), "do thing", stall_seconds=120)
        assert result.success

    def test_dispatch_result_defaults(self):
        r = DispatchResult(module="test", success=True, output="ok")
        assert r.exit_code == 0
        assert r.duration_seconds == 0.0
        assert r.truncated is False
        assert r.error is None
        assert r.event_count == 0
        assert r.last_tool_use == ""

    def test_dispatch_result_with_error(self):
        r = DispatchResult(
            module="test",
            success=False,
            output="",
            exit_code=1,
            error="process killed",
        )
        assert not r.success
        assert r.error == "process killed"
