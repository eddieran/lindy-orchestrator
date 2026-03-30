"""Compatibility re-exports for orchestrator helper functions."""

from .dispatch_core import extract_event_info
from .task_preparation import (
    _autofill_ci_params,
    _check_delivery,
    prepare_qa_checks,
)


def inject_qa_gates(*args, **kwargs):
    """Backward-compatible alias for prepare_qa_checks()."""
    return prepare_qa_checks(*args, **kwargs)


__all__ = [
    "_autofill_ci_params",
    "_check_delivery",
    "extract_event_info",
    "inject_qa_gates",
    "prepare_qa_checks",
]
