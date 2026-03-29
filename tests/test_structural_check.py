"""Tests for the structural lint gate."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.config import StructuralCheckConfig
from lindy_orchestrator.qa import Violation, format_violations
from lindy_orchestrator.qa.structural_check import (
    _check_file_size,
    _check_import_boundary,
    _check_sensitive_files,
    _get_staged_files,
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


def _fake_run(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestGetStagedFiles:
    """Tests for the _get_staged_files function and its fallback strategies."""

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_strategy1_staged_files(self, mock_run):
        """When staged files exist, return them directly."""
        mock_run.return_value = _fake_run(stdout="src/main.py\nsrc/utils.py")
        files = _get_staged_files(Path("/project"), "src/")
        assert files == ["src/main.py", "src/utils.py"]
        # Only one call needed (git diff --cached)
        assert mock_run.call_count == 1

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_strategy2_branch_diff_after_commit(self, mock_run):
        """When nothing is staged (agent committed), fall back to branch diff."""
        mock_run.side_effect = [
            # Strategy 1: git diff --cached → empty
            _fake_run(stdout=""),
            # Strategy 2: git merge-base main HEAD → sha
            _fake_run(stdout="abc123"),
            # Strategy 2: git diff --name-only abc123..HEAD
            _fake_run(stdout="src/modified.py\ndocs/README.md"),
        ]
        files = _get_staged_files(Path("/project"), "src/")
        assert files == ["src/modified.py"]
        assert mock_run.call_count == 3

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_strategy2_tries_master_if_main_fails(self, mock_run):
        """When 'main' branch doesn't exist, try 'master'."""
        mock_run.side_effect = [
            # Strategy 1: git diff --cached → empty
            _fake_run(stdout=""),
            # Strategy 2: git merge-base main HEAD → fails (no 'main')
            _fake_run(returncode=1),
            # Strategy 2: git merge-base master HEAD → sha
            _fake_run(stdout="def456"),
            # Strategy 2: git diff --name-only def456..HEAD
            _fake_run(stdout="backend/api.py"),
        ]
        files = _get_staged_files(Path("/project"), "backend/")
        assert files == ["backend/api.py"]

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_strategy3_fallback_all_tracked(self, mock_run):
        """When both staged and branch diff fail, fall back to all tracked."""
        mock_run.side_effect = [
            # Strategy 1: empty
            _fake_run(stdout=""),
            # Strategy 2 (main): merge-base fails
            _fake_run(returncode=1),
            # Strategy 2 (master): merge-base fails
            _fake_run(returncode=1),
            # Strategy 3: git ls-files
            _fake_run(stdout="a.py\nb.py\nc.py"),
        ]
        files = _get_staged_files(Path("/project"), "")
        assert files == ["a.py", "b.py", "c.py"]

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_strategy2_branch_diff_empty_falls_through(self, mock_run):
        """When branch diff returns empty (on main itself), fall back to ls-files."""
        mock_run.side_effect = [
            # Strategy 1: empty
            _fake_run(stdout=""),
            # Strategy 2 (main): merge-base succeeds
            _fake_run(stdout="abc123"),
            # Strategy 2 (main): diff returns empty (HEAD = main)
            _fake_run(stdout=""),
            # Strategy 2 (master): merge-base fails
            _fake_run(returncode=1),
            # Strategy 3: git ls-files
            _fake_run(stdout="x.py"),
        ]
        files = _get_staged_files(Path("/project"), "")
        assert files == ["x.py"]


class TestFormatViolations:
    def test_no_violations(self):
        result = format_violations([])
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
        result = format_violations(violations)
        assert "2 structural violation(s)" in result
        assert "VIOLATION [file_size]" in result
        assert "FIX: Split into" in result
        assert "VIOLATION [sensitive_file]" in result
        assert "FIX: Add to .gitignore" in result
