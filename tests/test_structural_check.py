"""Tests for the structural lint gate."""

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.config import StructuralCheckConfig
from lindy_orchestrator.qa.structural_check import (
    Violation,
    _check_file_size,
    _check_import_boundary,
    _check_sensitive_files,
    _format_violations,
    run_structural_check,
)


class TestFileSize:
    def test_under_limit(self, tmp_path: Path):
        f = tmp_path / "small.py"
        f.write_text("line\n" * 50)
        violations = _check_file_size(f, "small.py", max_lines=500)
        assert violations == []

    def test_over_limit(self, tmp_path: Path):
        f = tmp_path / "big.py"
        f.write_text("line\n" * 600)
        violations = _check_file_size(f, "big.py", max_lines=500)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule == "file_size"
        assert "600 lines" in v.message
        assert "500-line limit" in v.message
        assert "Split into" in v.remediation
        assert "big_core.py" in v.remediation

    def test_exactly_at_limit(self, tmp_path: Path):
        f = tmp_path / "exact.py"
        f.write_text("line\n" * 500)
        violations = _check_file_size(f, "exact.py", max_lines=500)
        assert violations == []


class TestSensitiveFiles:
    def test_env_file_flagged(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text("SECRET=value")
        violations = _check_sensitive_files(f, ".env", [".env", "*.key", "*.pem"])
        assert len(violations) == 1
        assert violations[0].rule == "sensitive_file"
        assert ".gitignore" in violations[0].remediation

    def test_key_file_flagged(self, tmp_path: Path):
        f = tmp_path / "server.key"
        f.write_text("private key")
        violations = _check_sensitive_files(f, "server.key", [".env", "*.key", "*.pem"])
        assert len(violations) == 1
        assert violations[0].rule == "sensitive_file"

    def test_normal_file_passes(self, tmp_path: Path):
        f = tmp_path / "main.py"
        f.write_text("print('hello')")
        violations = _check_sensitive_files(f, "main.py", [".env", "*.key", "*.pem"])
        assert violations == []


class TestImportBoundary:
    def test_cross_module_import_detected(self, tmp_path: Path):
        # Set up module structure
        backend = tmp_path / "backend"
        backend.mkdir()
        frontend = tmp_path / "frontend"
        frontend.mkdir()

        # Create a file with cross-module import
        bad_file = backend / "service.py"
        bad_file.write_text("from frontend.components import Button\n")

        violations = _check_import_boundary(tmp_path, "backend", ["backend/service.py"])
        assert len(violations) == 1
        assert violations[0].rule == "import_boundary"
        assert "frontend" in violations[0].message
        assert "CONTRACTS.md" in violations[0].remediation

    def test_same_module_import_ok(self, tmp_path: Path):
        backend = tmp_path / "backend"
        backend.mkdir()
        frontend = tmp_path / "frontend"
        frontend.mkdir()

        ok_file = backend / "service.py"
        ok_file.write_text("from backend.models import User\n")

        violations = _check_import_boundary(tmp_path, "backend", ["backend/service.py"])
        assert violations == []


class TestRunStructuralCheck:
    @patch("lindy_orchestrator.qa.structural_check._get_staged_files")
    def test_oversized_file_with_remediation(self, mock_staged, tmp_path: Path):
        # Create oversized file
        mod = tmp_path / "backend"
        mod.mkdir()
        big = mod / "auth.py"
        big.write_text("line\n" * 847)

        mock_staged.return_value = ["backend/auth.py"]

        config = StructuralCheckConfig(max_file_lines=500)
        passed, violations = run_structural_check(tmp_path, "backend", config)

        assert not passed
        assert len(violations) >= 1
        file_size_v = [v for v in violations if v.rule == "file_size"]
        assert len(file_size_v) == 1
        assert "847 lines" in file_size_v[0].message
        assert "Split into" in file_size_v[0].remediation


class TestFormatViolations:
    def test_no_violations(self):
        result = _format_violations([])
        assert "passed" in result.lower()

    def test_formats_violations(self):
        violations = [
            Violation(
                rule="file_size",
                file="auth.py",
                message="auth.py (847 lines) exceeds 500-line limit.",
                remediation="Split into auth_core.py and auth_helpers.py.",
            ),
            Violation(
                rule="sensitive_file",
                file=".env",
                message=".env matches sensitive pattern '.env'.",
                remediation="Add to .gitignore.",
            ),
        ]
        result = _format_violations(violations)
        assert "2 structural violation(s)" in result
        assert "VIOLATION [file_size]" in result
        assert "FIX: Split into" in result
        assert "VIOLATION [sensitive_file]" in result
        assert "FIX: Add to .gitignore" in result
