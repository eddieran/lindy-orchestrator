"""STATUS.md scaffold templates."""

from __future__ import annotations

from datetime import datetime, timezone


def generate_status_md(module_name: str) -> str:
    """Generate a standard STATUS.md for a module."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""\
# {module_name.title()} Status

## Meta
| Key | Value |
|-----|-------|
| module | {module_name} |
| last_updated | {now} |
| overall_health | GREEN |
| agent_session | — |

## Active Work
| ID | Task | Status | BlockedBy | Started | Notes |
|----|------|--------|-----------|---------|-------|

## Completed (Recent)
| ID | Task | Completed | Outcome |
|----|------|-----------|---------|

## Backlog
- (none)

## Cross-Module Requests
| ID | From | To | Request | Priority | Status |
|----|------|----|---------|----------|--------|

## Cross-Module Deliverables
| ID | From | To | Deliverable | Status | Path |
|----|------|----|-------------|--------|------|

## Key Metrics
| Metric | Value |
|--------|-------|

## Blockers
- (none)
"""
