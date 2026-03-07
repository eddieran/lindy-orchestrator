"""Shared data types for the entropy scanner.

Kept in a separate module to avoid circular imports between scanner.py and
scanner_helpers.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScanFinding:
    """A single scan finding."""

    category: str  # architecture_drift, contract_violation, status_drift, quality
    severity: str  # info, warning, error
    description: str
    file_path: str = ""
    remediation: str = ""


@dataclass
class ModuleGrade:
    """Quality grade for a single module."""

    module: str
    score: int  # 0-100
    grade: str  # A-F
    details: dict[str, int] = field(default_factory=dict)


@dataclass
class ScanReport:
    """Result of an entropy scan."""

    findings: list[ScanFinding] = field(default_factory=list)
    grades: list[ModuleGrade] = field(default_factory=list)

    def by_category(self) -> dict[str, list[ScanFinding]]:
        result: dict[str, list[ScanFinding]] = {}
        for f in self.findings:
            result.setdefault(f.category, []).append(f)
        return result

    def by_severity(self) -> dict[str, list[ScanFinding]]:
        result: dict[str, list[ScanFinding]] = {}
        for f in self.findings:
            result.setdefault(f.severity, []).append(f)
        return result
