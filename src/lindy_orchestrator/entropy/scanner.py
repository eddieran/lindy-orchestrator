"""Entropy scanner — detect architecture drift, contract violations, quality decay.

Produces a ScanReport with findings and per-module grades (A-F).
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..config import OrchestratorConfig
from .scanner_helpers import _grade_modules, _score_to_grade, format_scan_report  # noqa: F401 – re-exported
from .scanner_types import ModuleGrade, ScanFinding, ScanReport  # noqa: F401 – re-exported


def run_scan(config: OrchestratorConfig, module_filter: str | None = None) -> ScanReport:
    """Run all entropy checks and produce a graded report.

    Args:
        config: Orchestrator configuration.
        module_filter: If set, only scan this module.

    Returns:
        ScanReport with findings and module grades.
    """
    report = ScanReport()
    report.findings.extend(_check_architecture_drift(config))
    report.findings.extend(_check_contract_compliance(config))
    report.findings.extend(_check_status_consistency(config))
    report.findings.extend(_check_quality_metrics(config))
    report.grades = _grade_modules(config, report.findings)

    if module_filter:
        report.findings = [
            f
            for f in report.findings
            if module_filter in f.file_path or module_filter in f.description
        ]
        report.grades = [g for g in report.grades if g.module == module_filter]

    return report


# ---------------------------------------------------------------------------
# Check 1: Architecture drift
# ---------------------------------------------------------------------------


def _check_architecture_drift(config: OrchestratorConfig) -> list[ScanFinding]:
    """Compare ARCHITECTURE.md declarations against actual filesystem."""
    findings: list[ScanFinding] = []
    arch_path = config.root / "ARCHITECTURE.md"

    if not arch_path.exists():
        findings.append(
            ScanFinding(
                category="architecture_drift",
                severity="warning",
                description="ARCHITECTURE.md not found",
                file_path=str(arch_path),
                remediation="Run `lindy-orchestrate onboard` to generate ARCHITECTURE.md",
            )
        )
        return findings

    try:
        content = arch_path.read_text(encoding="utf-8")
    except OSError:
        return findings

    # Extract declared modules from ARCHITECTURE.md
    declared_modules: set[str] = set()
    for m in re.finditer(r"-\s+\*\*(\w+)/?\*\*", content):
        declared_modules.add(m.group(1))

    # Check declared modules exist on filesystem
    for mod_name in declared_modules:
        mod_path = config.root / mod_name
        if not mod_path.exists():
            findings.append(
                ScanFinding(
                    category="architecture_drift",
                    severity="error",
                    description=f"Declared module `{mod_name}/` does not exist on filesystem",
                    file_path=str(mod_path),
                    remediation=f"Create `{mod_name}/` directory or update ARCHITECTURE.md",
                )
            )

    # Check for undeclared module directories (that match config modules)
    for mod in config.modules:
        if mod.name not in declared_modules:
            mod_path = config.root / mod.path
            if mod_path.exists():
                findings.append(
                    ScanFinding(
                        category="architecture_drift",
                        severity="warning",
                        description=(
                            f"Config module `{mod.name}` exists but is not "
                            f"declared in ARCHITECTURE.md"
                        ),
                        file_path=str(arch_path),
                        remediation="Regenerate ARCHITECTURE.md or add the module manually",
                    )
                )

    # Check layer directories exist
    layer_pattern = re.compile(r"-\s+\*\*(\w+)/?\*\*:?\s*(.+)")
    for m in layer_pattern.finditer(content):
        mod_name = m.group(1)
        layer_str = m.group(2).strip()
        layers = [s.strip().lower() for s in re.split(r"\s*(?:→|->|,)\s*", layer_str) if s.strip()]
        mod_path = config.root / mod_name
        if mod_path.exists():
            for layer in layers:
                layer_path = mod_path / layer
                if not layer_path.exists() and not (mod_path / f"{layer}.py").exists():
                    findings.append(
                        ScanFinding(
                            category="architecture_drift",
                            severity="info",
                            description=(
                                f"Layer `{layer}` declared for `{mod_name}/` "
                                f"but directory/file not found"
                            ),
                            file_path=str(layer_path),
                            remediation=f"Create `{mod_name}/{layer}/` or update layer definition",
                        )
                    )

    return findings


# ---------------------------------------------------------------------------
# Check 2: Contract compliance
# ---------------------------------------------------------------------------


def _check_contract_compliance(config: OrchestratorConfig) -> list[ScanFinding]:
    """Check CONTRACTS.md completeness for multi-module projects."""
    findings: list[ScanFinding] = []

    if len(config.modules) < 2:
        return findings

    contracts_path = config.root / "CONTRACTS.md"
    if not contracts_path.exists():
        findings.append(
            ScanFinding(
                category="contract_violation",
                severity="warning",
                description="Multi-module project has no CONTRACTS.md",
                file_path=str(contracts_path),
                remediation="Run `lindy-orchestrate onboard` to generate CONTRACTS.md",
            )
        )
        return findings

    try:
        content = contracts_path.read_text(encoding="utf-8")
    except OSError:
        return findings

    # Check required sections
    required_sections = ["API", "Change Protocol"]
    for section in required_sections:
        if section.lower() not in content.lower():
            findings.append(
                ScanFinding(
                    category="contract_violation",
                    severity="warning",
                    description=f"CONTRACTS.md missing required section: {section}",
                    file_path=str(contracts_path),
                    remediation=f"Add a ## {section} section to CONTRACTS.md",
                )
            )

    # Check each module has a task ID prefix convention
    for mod in config.modules:
        # Look for module name reference in contracts
        if mod.name.lower() not in content.lower():
            findings.append(
                ScanFinding(
                    category="contract_violation",
                    severity="info",
                    description=f"Module `{mod.name}` not referenced in CONTRACTS.md",
                    file_path=str(contracts_path),
                    remediation=f"Add interface definitions for `{mod.name}` to CONTRACTS.md",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Check 3: STATUS.md consistency
# ---------------------------------------------------------------------------


def _check_status_consistency(config: OrchestratorConfig) -> list[ScanFinding]:
    """Check STATUS.md files for health and freshness."""
    findings: list[ScanFinding] = []

    for mod in config.modules:
        status_path = config.status_path(mod.name)
        if not status_path.exists():
            findings.append(
                ScanFinding(
                    category="status_drift",
                    severity="warning",
                    description=f"Module `{mod.name}` has no STATUS.md",
                    file_path=str(status_path),
                    remediation=f"Create STATUS.md for `{mod.name}` module",
                )
            )
            continue

        try:
            content = status_path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Check health value is valid
        health_match = re.search(
            r"Overall\s+Health\s*\|\s*(\w+)",
            content,
            re.IGNORECASE,
        )
        if health_match:
            health = health_match.group(1).upper()
            if health not in ("GREEN", "YELLOW", "RED"):
                findings.append(
                    ScanFinding(
                        category="status_drift",
                        severity="warning",
                        description=(f"`{mod.name}/STATUS.md` has invalid health value: {health}"),
                        file_path=str(status_path),
                        remediation="Health must be GREEN, YELLOW, or RED",
                    )
                )

        # Check freshness via file mtime
        try:
            mtime = datetime.fromtimestamp(status_path.stat().st_mtime, tz=timezone.utc)
            age_days = (datetime.now(timezone.utc) - mtime).days
            if age_days > 14:
                findings.append(
                    ScanFinding(
                        category="status_drift",
                        severity="warning",
                        description=(f"`{mod.name}/STATUS.md` last modified {age_days} days ago"),
                        file_path=str(status_path),
                        remediation="Update STATUS.md with current module state",
                    )
                )
        except OSError:
            pass

        # Check for IN_PROGRESS tasks with stale branches
        in_progress = re.findall(
            r"\|\s*(\w+-\d+)\s*\|[^|]*\|\s*IN_PROGRESS\s*\|",
            content,
            re.IGNORECASE,
        )
        for task_id in in_progress:
            branch = f"{config.project.branch_prefix}/task-{task_id}"
            if not _branch_exists(config.root, branch):
                findings.append(
                    ScanFinding(
                        category="status_drift",
                        severity="info",
                        description=(
                            f"`{mod.name}` has IN_PROGRESS task {task_id} "
                            f"but branch `{branch}` not found"
                        ),
                        file_path=str(status_path),
                        remediation=(f"Update task {task_id} status or create branch `{branch}`"),
                    )
                )

    return findings


def _branch_exists(project_root: Path, branch_name: str) -> bool:
    """Check if a git branch exists locally."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Check 4: Quality metrics
# ---------------------------------------------------------------------------


def _check_quality_metrics(config: OrchestratorConfig) -> list[ScanFinding]:
    """Check per-module code quality metrics."""
    findings: list[ScanFinding] = []

    for mod in config.modules:
        mod_path = config.root / mod.path
        if not mod_path.exists():
            continue

        # Count oversized files
        large_files = []
        total_files = 0
        total_lines = 0

        for f in mod_path.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java"):
                continue
            if any(
                p in f.parts
                for p in (
                    "node_modules",
                    "__pycache__",
                    ".venv",
                    "venv",
                    "dist",
                    "build",
                    ".eggs",
                    "target",
                )
            ):
                continue

            total_files += 1
            try:
                line_count = sum(1 for _ in f.open("r", encoding="utf-8", errors="replace"))
                total_lines += line_count
                if line_count > 500:
                    large_files.append((str(f.relative_to(config.root)), line_count))
            except OSError:
                continue

        if large_files:
            file_list = ", ".join(f"{name} ({lines}L)" for name, lines in large_files[:5])
            findings.append(
                ScanFinding(
                    category="quality",
                    severity="warning",
                    description=(
                        f"`{mod.name}` has {len(large_files)} file(s) over 500 lines: {file_list}"
                    ),
                    file_path=str(mod_path),
                    remediation="Split large files into smaller, focused modules",
                )
            )

        # Check test directory exists
        has_tests = any((mod_path / d).exists() for d in ("tests", "test", "__tests__", "spec"))
        if not has_tests and total_files > 0:
            findings.append(
                ScanFinding(
                    category="quality",
                    severity="warning",
                    description=f"`{mod.name}` has no test directory",
                    file_path=str(mod_path),
                    remediation=f"Create `{mod.path}/tests/` with initial test files",
                )
            )

    return findings
