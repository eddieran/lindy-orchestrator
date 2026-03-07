"""Grading and formatting helpers for the entropy scanner."""

from __future__ import annotations

from ..config import OrchestratorConfig
from .scanner_types import ModuleGrade, ScanFinding, ScanReport

_GRADE_THRESHOLDS = [
    (90, "A"),
    (75, "B"),
    (60, "C"),
    (40, "D"),
    (0, "F"),
]


def _score_to_grade(score: int) -> str:
    """Convert a numeric score (0-100) to a letter grade."""
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def _grade_modules(
    config: OrchestratorConfig,
    findings: list[ScanFinding],
) -> list[ModuleGrade]:
    """Grade each module based on findings.

    Scoring: starts at 100, deductions by category:
    - architecture_drift error: -30, warning: -15, info: -5
    - contract_violation warning: -10, info: -5
    - status_drift warning: -15, info: -5
    - quality warning: -10, info: -5
    """
    grades: list[ModuleGrade] = []

    for mod in config.modules:
        score = 100
        details: dict[str, int] = {
            "architecture": 0,
            "contracts": 0,
            "status": 0,
            "quality": 0,
        }

        for f in findings:
            # Match finding to module
            if mod.name not in f.description and mod.name not in f.file_path:
                # Check if it's a project-level finding (no specific module)
                if any(m.name in f.description or m.name in f.file_path for m in config.modules):
                    continue
                # Project-level findings affect all modules equally (reduced impact)
                penalty = _finding_penalty(f) // max(len(config.modules), 1)
            else:
                penalty = _finding_penalty(f)

            score -= penalty
            if f.category == "architecture_drift":
                details["architecture"] += penalty
            elif f.category == "contract_violation":
                details["contracts"] += penalty
            elif f.category == "status_drift":
                details["status"] += penalty
            elif f.category == "quality":
                details["quality"] += penalty

        score = max(0, min(100, score))
        grades.append(
            ModuleGrade(
                module=mod.name,
                score=score,
                grade=_score_to_grade(score),
                details=details,
            )
        )

    return grades


def _finding_penalty(f: ScanFinding) -> int:
    """Calculate penalty for a single finding."""
    penalties = {
        ("architecture_drift", "error"): 30,
        ("architecture_drift", "warning"): 15,
        ("architecture_drift", "info"): 5,
        ("contract_violation", "error"): 20,
        ("contract_violation", "warning"): 10,
        ("contract_violation", "info"): 5,
        ("status_drift", "error"): 20,
        ("status_drift", "warning"): 15,
        ("status_drift", "info"): 5,
        ("quality", "error"): 20,
        ("quality", "warning"): 10,
        ("quality", "info"): 5,
    }
    return penalties.get((f.category, f.severity), 5)


def format_scan_report(report: ScanReport, grade_only: bool = False) -> str:
    """Format a scan report for display."""
    lines: list[str] = []

    if not grade_only:
        if not report.findings:
            lines.append("No entropy detected. Codebase is clean.")
        else:
            by_cat = report.by_category()
            for category, findings in by_cat.items():
                label = category.replace("_", " ").title()
                lines.append(f"## {label} ({len(findings)})")
                for f in findings:
                    icon = {"error": "E", "warning": "W", "info": "I"}.get(f.severity, "?")
                    lines.append(f"  [{icon}] {f.description}")
                    if f.remediation:
                        lines.append(f"      FIX: {f.remediation}")
                lines.append("")

    if report.grades:
        lines.append("## Module Grades")
        lines.append("")
        for g in report.grades:
            detail_str = ", ".join(f"{k}=-{v}" for k, v in g.details.items() if v > 0)
            detail_suffix = f" ({detail_str})" if detail_str else ""
            lines.append(f"  {g.module}: {g.grade} ({g.score}/100){detail_suffix}")

    return "\n".join(lines)
