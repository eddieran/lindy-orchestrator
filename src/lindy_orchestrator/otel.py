"""Optional OpenTelemetry metrics exporter for lindy-orchestrator.

Guarded import — gracefully degrades when OTel SDK is not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, Meter

    from .config import OTelConfig

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded import
# ---------------------------------------------------------------------------

try:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )

    _otel_available = True
except ImportError:
    _otel_available = False


def is_otel_available() -> bool:
    """Return True if the OpenTelemetry SDK is installed."""
    return _otel_available


# ---------------------------------------------------------------------------
# Exporter factory
# ---------------------------------------------------------------------------


def create_otel_exporter(
    endpoint: str = "",
    exporter_type: str = "console",
) -> PeriodicExportingMetricReader:
    """Create a metric reader with the specified exporter backend.

    Args:
        endpoint: OTLP endpoint URL (required when exporter_type is 'otlp').
        exporter_type: 'console' or 'otlp'.

    Returns:
        A PeriodicExportingMetricReader wrapping the chosen exporter.
    """
    if not _otel_available:
        raise RuntimeError("OpenTelemetry SDK is not installed")

    if exporter_type == "otlp":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=not endpoint.startswith("https"))
    else:
        exporter = ConsoleMetricExporter()

    return PeriodicExportingMetricReader(exporter)


# ---------------------------------------------------------------------------
# OTelMetricsExporter — hooks into HookRegistry via on_any()
# ---------------------------------------------------------------------------


class OTelMetricsExporter:
    """Records OTel metrics from orchestrator lifecycle events."""

    def __init__(self, meter: Meter) -> None:
        self._meter = meter
        self._handler = self._on_event
        self._hooks: object | None = None

        # Instruments
        self._task_duration: Histogram = meter.create_histogram(
            name="lindy.task.duration",
            description="Task execution duration",
            unit="s",
        )
        self._task_cost: Histogram = meter.create_histogram(
            name="lindy.task.cost",
            description="Task execution cost",
            unit="USD",
        )
        self._task_completed: Counter = meter.create_counter(
            name="lindy.task.completed",
            description="Number of completed tasks",
        )
        self._task_failed: Counter = meter.create_counter(
            name="lindy.task.failed",
            description="Number of failed tasks",
        )
        self._task_skipped: Counter = meter.create_counter(
            name="lindy.task.skipped",
            description="Number of skipped tasks",
        )
        self._dispatch_count: Counter = meter.create_counter(
            name="lindy.dispatch.count",
            description="Number of dispatches",
        )
        self._qa_passed: Counter = meter.create_counter(
            name="lindy.qa.passed",
            description="Number of QA gates passed",
        )
        self._qa_failed: Counter = meter.create_counter(
            name="lindy.qa.failed",
            description="Number of QA gates failed",
        )
        self._stall_warning: Counter = meter.create_counter(
            name="lindy.stall.warning",
            description="Number of stall warnings",
        )

    def attach(self, hooks: object) -> None:
        """Register this exporter as an on_any handler on the HookRegistry."""
        from .hooks import HookRegistry

        if not isinstance(hooks, HookRegistry):
            raise TypeError(f"Expected HookRegistry, got {type(hooks).__name__}")
        self._hooks = hooks
        hooks.on_any(self._handler)

    def detach(self) -> None:
        """Remove the on_any handler from the HookRegistry."""
        from .hooks import HookRegistry

        if self._hooks is not None and isinstance(self._hooks, HookRegistry):
            self._hooks.remove_any(self._handler)
            self._hooks = None

    def _on_event(self, event: object) -> None:
        """Handle any orchestrator event and record metrics."""
        from .hooks import Event, EventType

        if not isinstance(event, Event):
            return

        attrs = {"module": event.module or "unknown"}

        match event.type:
            case EventType.TASK_COMPLETED:
                self._task_completed.add(1, attrs)
                duration = event.data.get("duration_seconds")
                if duration is not None:
                    self._task_duration.record(float(duration), attrs)
                cost = event.data.get("cost_usd")
                if cost is not None:
                    self._task_cost.record(float(cost), attrs)
            case EventType.TASK_FAILED:
                self._task_failed.add(1, attrs)
            case EventType.TASK_SKIPPED:
                self._task_skipped.add(1, attrs)
            case EventType.TASK_STARTED:
                self._dispatch_count.add(1, attrs)
            case EventType.QA_PASSED:
                self._qa_passed.add(1, attrs)
            case EventType.QA_FAILED:
                self._qa_failed.add(1, attrs)
            case EventType.STALL_WARNING:
                self._stall_warning.add(1, attrs)


# ---------------------------------------------------------------------------
# High-level setup from config
# ---------------------------------------------------------------------------


def setup_otel_from_config(otel_config: OTelConfig) -> OTelMetricsExporter | None:
    """Create and configure an OTelMetricsExporter from config.

    Returns None if OTel is disabled or the SDK is not installed.
    """
    if not otel_config.enabled:
        return None

    if not _otel_available:
        log.warning("OTel is enabled in config but opentelemetry-sdk is not installed")
        return None

    reader = create_otel_exporter(
        endpoint=otel_config.endpoint,
        exporter_type=otel_config.exporter,
    )
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    meter = metrics.get_meter(otel_config.service_name)
    return OTelMetricsExporter(meter)
