"""Factory for creating tracker provider instances."""

from __future__ import annotations

from typing import Any

from .base import TrackerProvider


def create_tracker(provider: str = "github", **kwargs: Any) -> TrackerProvider:
    """Create a tracker provider instance.

    Args:
        provider: Provider name ("github" or "linear").
        **kwargs: Provider-specific arguments (e.g., repo="owner/repo").

    Returns:
        A TrackerProvider instance.
    """
    if provider == "github":
        from .github_issues import GitHubIssuesProvider

        return GitHubIssuesProvider(repo=kwargs.get("repo", ""))

    raise ValueError(f"Unknown tracker provider: {provider!r}. Available: github")
