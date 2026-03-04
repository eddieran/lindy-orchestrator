"""Tests for the entropy scanner."""

import os
import time
from pathlib import Path

from lindy_orchestrator.config import (
    ModuleConfig,
    OrchestratorConfig,
    ProjectConfig,
)
from lindy_orchestrator.entropy.scanner import (
    ModuleGrade,
    ScanFinding,
    ScanReport,
    _check_architecture_drift,
    _check_contract_compliance,
    _check_quality_metrics,
    _check_status_consistency,
    _grade_modules,
    _score_to_grade,
    format_scan_report,
    run_scan,
)


def _make_config(tmp_path: Path, modules=None) -> OrchestratorConfig:
    """Create a test config rooted at tmp_path."""
    if modules is None:
        modules = [
            ModuleConfig(name="backend", path="backend"),
            ModuleConfig(name="frontend", path="frontend"),
        ]
    cfg = OrchestratorConfig(
        project=ProjectConfig(name="test-project", branch_prefix="af"),
        modules=modules,
    )
    cfg._config_dir = tmp_path
    return cfg


# ---------------------------------------------------------------------------
# ScanReport
# ---------------------------------------------------------------------------


class TestScanReport:
    def test_empty_report(self):
        report = ScanReport()
        assert report.findings == []
        assert report.grades == []

    def test_by_category(self):
        report = ScanReport(
            findings=[
                ScanFinding(category="quality", severity="warning", description="a"),
                ScanFinding(category="quality", severity="error", description="b"),
                ScanFinding(category="status_drift", severity="warning", description="c"),
            ]
        )
        by_cat = report.by_category()
        assert len(by_cat["quality"]) == 2
        assert len(by_cat["status_drift"]) == 1

    def test_by_severity(self):
        report = ScanReport(
            findings=[
                ScanFinding(category="quality", severity="warning", description="a"),
                ScanFinding(category="quality", severity="error", description="b"),
                ScanFinding(category="status_drift", severity="warning", description="c"),
            ]
        )
        by_sev = report.by_severity()
        assert len(by_sev["warning"]) == 2
        assert len(by_sev["error"]) == 1


# ---------------------------------------------------------------------------
# Architecture drift
# ---------------------------------------------------------------------------


class TestArchitectureDrift:
    def test_no_architecture_md(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        findings = _check_architecture_drift(cfg)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "not found" in findings[0].description

    def test_missing_declared_module(self, tmp_path: Path):
        arch = tmp_path / "ARCHITECTURE.md"
        arch.write_text("## Module Topology\n\n- **backend/** → Python\n- **ghost/** → ???\n")
        (tmp_path / "backend").mkdir()
        cfg = _make_config(tmp_path)
        findings = _check_architecture_drift(cfg)
        missing = [f for f in findings if "ghost" in f.description and f.severity == "error"]
        assert len(missing) == 1

    def test_undeclared_config_module(self, tmp_path: Path):
        arch = tmp_path / "ARCHITECTURE.md"
        arch.write_text("## Module Topology\n\n- **backend/** → Python\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        cfg = _make_config(tmp_path)
        findings = _check_architecture_drift(cfg)
        undeclared = [
            f for f in findings if "frontend" in f.description and "not declared" in f.description
        ]
        assert len(undeclared) == 1

    def test_clean_architecture(self, tmp_path: Path):
        arch = tmp_path / "ARCHITECTURE.md"
        arch.write_text("## Module Topology\n\n- **backend/** → Python\n- **frontend/** → React\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        cfg = _make_config(tmp_path)
        findings = _check_architecture_drift(cfg)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == []

    def test_missing_layer_directory(self, tmp_path: Path):
        arch = tmp_path / "ARCHITECTURE.md"
        arch.write_text(
            "## Layer Structure\n\n- **backend/**: models → schemas → services → routes → main\n"
        )
        be = tmp_path / "backend"
        be.mkdir()
        (be / "models").mkdir()
        # schemas, services, routes missing — only 'main' exists as main.py
        cfg = _make_config(tmp_path)
        findings = _check_architecture_drift(cfg)
        layer_findings = [f for f in findings if "Layer" in f.description]
        assert len(layer_findings) >= 1


# ---------------------------------------------------------------------------
# Contract compliance
# ---------------------------------------------------------------------------


class TestContractCompliance:
    def test_single_module_skips(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="app", path="app")])
        findings = _check_contract_compliance(cfg)
        assert findings == []

    def test_missing_contracts_md(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        findings = _check_contract_compliance(cfg)
        assert len(findings) == 1
        assert "no CONTRACTS.md" in findings[0].description

    def test_missing_section(self, tmp_path: Path):
        contracts = tmp_path / "CONTRACTS.md"
        contracts.write_text("# Contracts\n\nSome content but no sections.\n")
        cfg = _make_config(tmp_path)
        findings = _check_contract_compliance(cfg)
        missing = [f for f in findings if "missing required section" in f.description]
        assert len(missing) >= 1

    def test_complete_contracts(self, tmp_path: Path):
        contracts = tmp_path / "CONTRACTS.md"
        contracts.write_text(
            "# Contracts\n\n"
            "## API Contracts\n\n"
            "### backend endpoints\n\n"
            "### frontend endpoints\n\n"
            "## Change Protocol\n\n"
            "Steps to update contracts.\n"
        )
        cfg = _make_config(tmp_path)
        findings = _check_contract_compliance(cfg)
        section_findings = [f for f in findings if "missing required section" in f.description]
        assert section_findings == []

    def test_module_not_referenced(self, tmp_path: Path):
        contracts = tmp_path / "CONTRACTS.md"
        contracts.write_text(
            "# Contracts\n\n## API Contracts\n\nbackend stuff\n\n## Change Protocol\n\nSteps.\n"
        )
        cfg = _make_config(tmp_path)
        findings = _check_contract_compliance(cfg)
        missing_mod = [
            f for f in findings if "frontend" in f.description and "not referenced" in f.description
        ]
        assert len(missing_mod) == 1


# ---------------------------------------------------------------------------
# Status consistency
# ---------------------------------------------------------------------------


class TestStatusConsistency:
    def test_missing_status_md(self, tmp_path: Path):
        cfg = _make_config(tmp_path)
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        findings = _check_status_consistency(cfg)
        missing = [f for f in findings if "no STATUS.md" in f.description]
        assert len(missing) == 2

    def test_invalid_health_value(self, tmp_path: Path):
        be = tmp_path / "backend"
        be.mkdir()
        status = be / "STATUS.md"
        status.write_text("| Key | Value |\n| Overall Health | PURPLE |\n")
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = _check_status_consistency(cfg)
        invalid = [f for f in findings if "invalid health" in f.description]
        assert len(invalid) == 1
        assert "PURPLE" in invalid[0].description

    def test_valid_health_values(self, tmp_path: Path):
        for health in ("GREEN", "YELLOW", "RED"):
            be = tmp_path / f"mod_{health}"
            be.mkdir()
            status = be / "STATUS.md"
            status.write_text(f"| Key | Value |\n| Overall Health | {health} |\n")
            cfg = _make_config(
                tmp_path,
                modules=[ModuleConfig(name=f"mod_{health}", path=f"mod_{health}")],
            )
            findings = _check_status_consistency(cfg)
            invalid = [f for f in findings if "invalid health" in f.description]
            assert invalid == [], f"Health {health} should be valid"

    def test_stale_status_md(self, tmp_path: Path):
        be = tmp_path / "backend"
        be.mkdir()
        status = be / "STATUS.md"
        status.write_text("| Key | Value |\n| Overall Health | GREEN |\n")
        # Make the file look old
        old_time = time.time() - (20 * 86400)  # 20 days ago
        os.utime(status, (old_time, old_time))
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = _check_status_consistency(cfg)
        stale = [f for f in findings if "days ago" in f.description]
        assert len(stale) == 1


# ---------------------------------------------------------------------------
# Quality metrics
# ---------------------------------------------------------------------------


class TestQualityMetrics:
    def test_large_file_detected(self, tmp_path: Path):
        be = tmp_path / "backend"
        be.mkdir()
        big = be / "huge.py"
        big.write_text("x = 1\n" * 600)
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = _check_quality_metrics(cfg)
        large = [f for f in findings if "over 500 lines" in f.description]
        assert len(large) == 1

    def test_no_tests_directory(self, tmp_path: Path):
        be = tmp_path / "backend"
        be.mkdir()
        (be / "main.py").write_text("x = 1\n")
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = _check_quality_metrics(cfg)
        no_tests = [f for f in findings if "no test directory" in f.description]
        assert len(no_tests) == 1

    def test_clean_module(self, tmp_path: Path):
        be = tmp_path / "backend"
        be.mkdir()
        (be / "main.py").write_text("x = 1\n" * 50)
        (be / "tests").mkdir()
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = _check_quality_metrics(cfg)
        assert findings == []


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


class TestGrading:
    def test_score_to_grade(self):
        assert _score_to_grade(100) == "A"
        assert _score_to_grade(95) == "A"
        assert _score_to_grade(90) == "A"
        assert _score_to_grade(89) == "B"
        assert _score_to_grade(75) == "B"
        assert _score_to_grade(74) == "C"
        assert _score_to_grade(60) == "C"
        assert _score_to_grade(59) == "D"
        assert _score_to_grade(40) == "D"
        assert _score_to_grade(39) == "F"
        assert _score_to_grade(0) == "F"

    def test_perfect_score(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        grades = _grade_modules(cfg, [])
        assert len(grades) == 1
        assert grades[0].score == 100
        assert grades[0].grade == "A"

    def test_error_penalty(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = [
            ScanFinding(
                category="architecture_drift",
                severity="error",
                description="backend module missing",
                file_path="backend/",
            )
        ]
        grades = _grade_modules(cfg, findings)
        assert grades[0].score == 70  # 100 - 30
        assert grades[0].grade == "C"

    def test_warning_penalty(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = [
            ScanFinding(
                category="quality",
                severity="warning",
                description="backend has large files",
                file_path="backend/",
            )
        ]
        grades = _grade_modules(cfg, findings)
        assert grades[0].score == 90  # 100 - 10
        assert grades[0].grade == "A"

    def test_multiple_penalties(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = [
            ScanFinding(
                category="architecture_drift", severity="error", description="backend missing"
            ),
            ScanFinding(category="quality", severity="warning", description="backend large files"),
            ScanFinding(category="status_drift", severity="warning", description="backend stale"),
        ]
        grades = _grade_modules(cfg, findings)
        # 100 - 30 - 10 - 15 = 45
        assert grades[0].score == 45
        assert grades[0].grade == "D"

    def test_f_grade(self, tmp_path: Path):
        cfg = _make_config(tmp_path, modules=[ModuleConfig(name="backend", path="backend")])
        findings = [
            ScanFinding(category="architecture_drift", severity="error", description="backend a"),
            ScanFinding(category="architecture_drift", severity="error", description="backend b"),
            ScanFinding(category="quality", severity="warning", description="backend c"),
            ScanFinding(category="status_drift", severity="warning", description="backend d"),
        ]
        grades = _grade_modules(cfg, findings)
        # 100 - 30 - 30 - 10 - 15 = 15
        assert grades[0].score == 15
        assert grades[0].grade == "F"


# ---------------------------------------------------------------------------
# format_scan_report
# ---------------------------------------------------------------------------


class TestFormatScanReport:
    def test_empty_report(self):
        report = ScanReport()
        output = format_scan_report(report)
        assert "clean" in output.lower()

    def test_grade_only(self):
        report = ScanReport(
            findings=[ScanFinding(category="quality", severity="warning", description="something")],
            grades=[ModuleGrade(module="backend", score=90, grade="A", details={})],
        )
        output = format_scan_report(report, grade_only=True)
        assert "Module Grades" in output
        assert "backend" in output
        # Should not include detailed findings
        assert "something" not in output

    def test_full_report_includes_findings_and_grades(self):
        report = ScanReport(
            findings=[ScanFinding(category="quality", severity="warning", description="big files")],
            grades=[ModuleGrade(module="backend", score=90, grade="A", details={"quality": 10})],
        )
        output = format_scan_report(report)
        assert "big files" in output
        assert "Module Grades" in output
        assert "backend" in output


# ---------------------------------------------------------------------------
# run_scan integration
# ---------------------------------------------------------------------------


class TestRunScan:
    def test_basic_scan(self, tmp_path: Path):
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        cfg = _make_config(tmp_path)
        report = run_scan(cfg)
        assert isinstance(report, ScanReport)
        assert isinstance(report.grades, list)

    def test_module_filter(self, tmp_path: Path):
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        cfg = _make_config(tmp_path)
        report = run_scan(cfg, module_filter="backend")
        # Should only have backend grades
        for g in report.grades:
            assert g.module == "backend"

    def test_scan_with_architecture(self, tmp_path: Path):
        arch = tmp_path / "ARCHITECTURE.md"
        arch.write_text("## Module Topology\n\n- **backend/** → Python\n- **frontend/** → React\n")
        (tmp_path / "backend").mkdir()
        (tmp_path / "frontend").mkdir()
        cfg = _make_config(tmp_path)
        report = run_scan(cfg)
        # Should have grades for both modules
        assert len(report.grades) == 2
