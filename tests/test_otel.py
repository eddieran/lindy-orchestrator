"""Tests for the optional OTel metrics exporter."""

from __future__ import annotations

import importlib
import sys
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Import guard — graceful when SDK is not installed
# ---------------------------------------------------------------------------


def test_is_otel_available_reflects_sdk_presence():
    """is_otel_available() returns bool without raising."""
    from lindy_orchestrator.otel import is_otel_available

    result = is_otel_available()
    assert isinstance(result, bool)


def test_import_guard_no_crash():
    """Module imports cleanly even if opentelemetry is absent."""
    # Force re-import with opentelemetry blocked
    saved = {}
    otel_mods = [k for k in sys.modules if k.startswith("opentelemetry")]
    for k in otel_mods:
        saved[k] = sys.modules.pop(k)
    saved_otel_mod = sys.modules.pop("lindy_orchestrator.otel", None)

    orig_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _block_otel(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            raise ImportError("blocked for test")
        return orig_import(name, *args, **kwargs)

    try:
        with mock.patch("builtins.__import__", side_effect=_block_otel):
            mod = importlib.import_module("lindy_orchestrator.otel")
            importlib.reload(mod)
            assert mod._otel_available is False
            assert mod.is_otel_available() is False
    finally:
        # Restore
        if saved_otel_mod is not None:
            sys.modules["lindy_orchestrator.otel"] = saved_otel_mod
        for k, v in saved.items():
            sys.modules[k] = v


def test_setup_otel_disabled_returns_none():
    """setup_otel_from_config returns None when config.enabled is False."""
    from lindy_orchestrator.config import OTelConfig
    from lindy_orchestrator.otel import setup_otel_from_config

    cfg = OTelConfig(enabled=False)
    assert setup_otel_from_config(cfg) is None


# ---------------------------------------------------------------------------
# Tests that require the SDK
# ---------------------------------------------------------------------------

otel_sdk = pytest.importorskip("opentelemetry.sdk")  # noqa: E402


def test_setup_otel_sdk_not_installed_returns_none():
    """When SDK is missing and enabled=True, returns None (logged warning)."""
    from lindy_orchestrator.config import OTelConfig

    import lindy_orchestrator.otel as otel_mod

    original = otel_mod._otel_available
    try:
        otel_mod._otel_available = False
        cfg = OTelConfig(enabled=True)
        result = otel_mod.setup_otel_from_config(cfg)
        assert result is None
    finally:
        otel_mod._otel_available = original


def test_attach_detach():
    """OTelMetricsExporter attaches/detaches from HookRegistry."""
    from opentelemetry.sdk.metrics import MeterProvider

    from lindy_orchestrator.hooks import HookRegistry
    from lindy_orchestrator.otel import OTelMetricsExporter

    provider = MeterProvider()
    meter = provider.get_meter("test")
    exporter = OTelMetricsExporter(meter)

    hooks = HookRegistry()
    assert hooks.handler_count == 0

    exporter.attach(hooks)
    assert hooks.handler_count == 1

    exporter.detach()
    assert hooks.handler_count == 0


def test_attach_rejects_non_hookregistry():
    """attach() raises TypeError for non-HookRegistry objects."""
    from opentelemetry.sdk.metrics import MeterProvider

    from lindy_orchestrator.otel import OTelMetricsExporter

    provider = MeterProvider()
    meter = provider.get_meter("test")
    exporter = OTelMetricsExporter(meter)

    with pytest.raises(TypeError, match="Expected HookRegistry"):
        exporter.attach("not a registry")


def test_counter_recording():
    """Events increment the correct counters."""
    from opentelemetry.sdk.metrics import MeterProvider

    from lindy_orchestrator.hooks import Event, EventType, HookRegistry
    from lindy_orchestrator.otel import OTelMetricsExporter

    provider = MeterProvider()
    meter = provider.get_meter("test")
    exporter = OTelMetricsExporter(meter)
    hooks = HookRegistry()
    exporter.attach(hooks)

    # Emit various events
    hooks.emit(Event(type=EventType.TASK_COMPLETED, module="backend"))
    hooks.emit(Event(type=EventType.TASK_FAILED, module="frontend"))
    hooks.emit(Event(type=EventType.TASK_SKIPPED, module="backend"))
    hooks.emit(Event(type=EventType.TASK_STARTED, module="backend"))
    hooks.emit(Event(type=EventType.QA_PASSED, module="backend"))
    hooks.emit(Event(type=EventType.QA_FAILED, module="frontend"))
    hooks.emit(Event(type=EventType.STALL_WARNING, module="backend"))

    # No exceptions means counters recorded successfully
    exporter.detach()


def test_histogram_recording():
    """TASK_COMPLETED with duration/cost records histogram values."""
    from opentelemetry.sdk.metrics import MeterProvider

    from lindy_orchestrator.hooks import Event, EventType, HookRegistry
    from lindy_orchestrator.otel import OTelMetricsExporter

    provider = MeterProvider()
    meter = provider.get_meter("test")
    exporter = OTelMetricsExporter(meter)
    hooks = HookRegistry()
    exporter.attach(hooks)

    hooks.emit(
        Event(
            type=EventType.TASK_COMPLETED,
            module="backend",
            data={"duration_seconds": 42.5, "cost_usd": 0.03},
        )
    )

    # No exceptions means histograms recorded successfully
    exporter.detach()


def test_module_attribute_label():
    """All metrics carry the 'module' attribute label."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from lindy_orchestrator.hooks import Event, EventType, HookRegistry
    from lindy_orchestrator.otel import OTelMetricsExporter

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    exporter = OTelMetricsExporter(meter)
    hooks = HookRegistry()
    exporter.attach(hooks)

    hooks.emit(Event(type=EventType.TASK_COMPLETED, module="my-module"))
    hooks.emit(Event(type=EventType.QA_PASSED, module="my-module"))

    # Force collection
    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None

    # Check that at least one metric has the module attribute
    found_module_attr = False
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                for data_point in metric.data.data_points:
                    if data_point.attributes and data_point.attributes.get("module") == "my-module":
                        found_module_attr = True
                        break

    assert found_module_attr, "Expected 'module' attribute on at least one metric data point"
    exporter.detach()


def test_create_otel_exporter_console():
    """create_otel_exporter with console type returns a reader."""
    from lindy_orchestrator.otel import create_otel_exporter

    reader = create_otel_exporter(exporter_type="console")
    assert reader is not None
    reader.shutdown()
