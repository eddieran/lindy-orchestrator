"""Issue tracker integration for lindy-orchestrator.

Provides a pluggable interface for fetching issues and syncing status
back to external trackers (GitHub Issues, Linear, etc.).
"""

from __future__ import annotations

from .base import TrackerIssue, TrackerProvider
from .factory import create_tracker

__all__ = ["TrackerIssue", "TrackerProvider", "create_tracker"]
