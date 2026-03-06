"""Base protocol and data types for issue tracker integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class TrackerIssue:
    """Normalized issue from any tracker."""

    id: str
    title: str
    body: str
    labels: list[str] = field(default_factory=list)
    status: str = "open"
    priority: str = "normal"
    url: str = ""


@runtime_checkable
class TrackerProvider(Protocol):
    """Interface for issue tracker backends."""

    def fetch_issues(
        self,
        project: str,
        labels: list[str] | None = None,
        status: str = "open",
        limit: int = 20,
    ) -> list[TrackerIssue]: ...

    def update_status(self, issue_id: str, status: str, comment: str = "") -> bool: ...

    def add_comment(self, issue_id: str, comment: str) -> bool: ...
