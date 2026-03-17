"""Optional OpenTelemetry metrics exporter.

Requires ``opentelemetry-api``, ``opentelemetry-sdk``, and an OTLP exporter
package.  All imports are deferred so that the module can be imported without
those packages installed — ``setup_otel_from_config`` raises ``ImportError``
at call time if they are missing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OtelConfig
    from .hooks import Event, HookRegistry

log = logging.getLogger(__name__)


class OTelMetricsExporter:
    """Listens to orchestrator hook events and pushes metrics via OTel."""

    def __init__(self, provider: object, meter: object) -> None:
        self._provider = provider
        self._meter = meter
        self._hooks: HookRegistry | None = None
        self._handler = None

        # Counters / histograms
        self._dispatches = meter.create_counter(  # type: ignore[union-attr]
            "orchestrator.dispatches",
            description="Number of task dispatches",
        )
        self._task_completed = meter.create_counter(  # type: ignore[union-attr]
            "orchestrator.tasks.completed",
            description="Tasks completed",
        )
        self._task_failed = meter.create_counter(  # type: ignore[union-attr]
            "orchestrator.tasks.failed",
            description="Tasks failed",
        )
        self._qa_passed = meter.create_counter(  # type: ignore[union-attr]
            "orchestrator.qa.passed",
            description="QA gates passed",
        )
        self._qa_failed = meter.create_counter(  # type: ignore[union-attr]
            "orchestrator.qa.failed",
            description="QA gates failed",
        )

    # -- lifecycle ------------------------------------------------------------

    def attach(self, hooks: HookRegistry) -> None:
        """Register as an on_any listener on *hooks*."""
        self._hooks = hooks
        self._handler = self._on_event
        hooks.on_any(self._handler)

    def detach(self) -> None:
        """Remove ourselves from the hook registry."""
        if self._hooks and self._handler:
            self._hooks.remove_any(self._handler)
        self._hooks = None
        self._handler = None

    def shutdown(self) -> None:
        """Flush pending metrics and release SDK resources."""
        try:
            self._provider.shutdown()  # type: ignore[union-attr]
        except Exception:
            log.warning("OTel meter provider shutdown error", exc_info=True)

    # -- event handler --------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        from .hooks import EventType

        etype = event.type
        attrs = {"module": event.module} if event.module else {}

        if etype == EventType.TASK_COMPLETED:
            self._task_completed.add(1, attrs)
        elif etype == EventType.TASK_FAILED:
            self._task_failed.add(1, attrs)
        elif etype == EventType.QA_PASSED:
            self._qa_passed.add(1, attrs)
        elif etype == EventType.QA_FAILED:
            self._qa_failed.add(1, attrs)


def setup_otel_from_config(config: OtelConfig) -> OTelMetricsExporter:
    """Create an :class:`OTelMetricsExporter` from *config*.

    Raises :class:`ImportError` if ``opentelemetry`` packages are not
    installed.
    """
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": config.service_name})

    exporter_kwargs: dict[str, str] = {}
    if config.endpoint:
        exporter_kwargs["endpoint"] = config.endpoint

    reader = PeriodicExportingMetricReader(OTLPMetricExporter(**exporter_kwargs))
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    meter = provider.get_meter("lindy-orchestrator")
    return OTelMetricsExporter(provider, meter)
