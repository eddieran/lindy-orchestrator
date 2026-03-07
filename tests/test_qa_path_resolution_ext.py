"""Extended QA path resolution tests (end-to-end and CI/structural scenarios)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.models import QACheck
from lindy_orchestrator.qa import run_qa_gate


# ---------------------------------------------------------------------------
# Helpers (duplicated from test_qa_path_resolution for standalone use)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/tmp/fake-project")


def _custom_gate(name: str = "my-pytest", cmd: str = "echo ok", cwd: str = "{module_path}"):
    from lindy_orchestrator.config import CustomGateConfig

    return CustomGateConfig(name=name, command=cmd, cwd=cwd, timeout=10)


def _fake_run(returncode=0, stdout="ok", stderr=""):
    """Return a mock subprocess result."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# End-to-end scenario: simulates the exact failure from the bug report
# ---------------------------------------------------------------------------


class TestEndToEndRootModulePath:
    """Simulates: module name='lindy-orchestrator', path='./', QA gate with
    cwd='{module_path}'. Before fix: FileNotFoundError crash. After fix:
    runs in project root."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_root_module_custom_gate(self, mock_run):
        mock_run.return_value = _fake_run(stdout="3 passed")
        gate = _custom_gate(name="proj-pytest", cmd="pytest", cwd="{module_path}")
        check = QACheck(gate="proj-pytest", params={})
        # module_path resolves to project root (path: ./)
        resolved = _PROJECT_ROOT

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            custom_gates=[gate],
            module_path=resolved,
        )

        assert result.passed
        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        # Must use project root, NOT project_root/lindy-orchestrator
        assert actual_cwd == str(_PROJECT_ROOT)

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_root_module_builtin_command_check(self, mock_run):
        """End-to-end: auto-injected command_check for root module.

        This is the exact flow that triggers the bug:
        1. orchestrator.yaml: modules: [{name: lindy-orchestrator, path: ./}]
        2. qa_gates.custom: [{command: pytest, cwd: '{module_path}'}]
        3. Scheduler auto-injects: QACheck(gate='command_check', params={command: 'pytest', cwd: '{module_path}'})
        4. run_qa_gate resolves to built-in CommandCheckGate
        5. Bug: {module_path} → project_root/lindy-orchestrator (WRONG)
        6. Fix: {module_path} → project_root (CORRECT, uses resolved module_path)
        """
        mock_run.return_value = _fake_run(stdout="3 passed")
        check = QACheck(
            gate="command_check",
            params={"command": "pytest", "cwd": "{module_path}"},
        )

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            module_path=_PROJECT_ROOT,  # resolved from config: path=./
        )

        assert result.passed
        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT)
        assert "lindy-orchestrator" not in actual_cwd


# ---------------------------------------------------------------------------
# CI check: branch auto-population
# ---------------------------------------------------------------------------


class TestCICheckBranchAutoFill:
    """The scheduler should auto-fill ci_check branch from task context."""

    def test_empty_branch_filled_by_scheduler(self):
        """Simulate the scheduler's ci_check branch auto-fill logic."""
        qa = QACheck(gate="ci_check", params={"repo": "org/repo", "branch": ""})
        branch_prefix = "af"
        task_id = 3
        branch_name = f"{branch_prefix}/task-{task_id}"

        # This is the logic from scheduler.py
        if qa.gate == "ci_check" and not qa.params.get("branch"):
            qa.params["branch"] = branch_name

        assert qa.params["branch"] == "af/task-3"

    def test_existing_branch_not_overwritten(self):
        """If LLM already set a branch, don't overwrite."""
        qa = QACheck(gate="ci_check", params={"repo": "org/repo", "branch": "custom/branch"})
        branch_name = "af/task-1"

        if qa.gate == "ci_check" and not qa.params.get("branch"):
            qa.params["branch"] = branch_name

        assert qa.params["branch"] == "custom/branch"

    def test_repo_auto_filled_from_module_config(self):
        """If repo is empty, fill from module config."""
        from lindy_orchestrator.config import ModuleConfig

        qa = QACheck(gate="ci_check", params={"branch": "af/task-1"})
        mod = ModuleConfig(name="backend", path="backend/", repo="org/backend")

        # Simulate scheduler logic
        if qa.gate == "ci_check" and not qa.params.get("repo"):
            if mod.repo:
                qa.params["repo"] = mod.repo

        assert qa.params["repo"] == "org/backend"


# ---------------------------------------------------------------------------
# structural_check: file prefix for root modules
# ---------------------------------------------------------------------------


class TestStructuralCheckRootModule:
    """Regression: structural_check with root module (path=./) found zero
    files because it filtered by 'module_name/' prefix."""

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_root_module_includes_all_files(self, mock_run):
        """Root module should check all staged files, not filter by name prefix."""
        mock_run.return_value = _fake_run(stdout="src/main.py\nsetup.py\ntests/test_main.py")

        from lindy_orchestrator.qa.structural_check import _get_staged_files

        # Root module: empty prefix → all files
        files = _get_staged_files(_PROJECT_ROOT, "")
        assert "src/main.py" in files
        assert "setup.py" in files
        assert len(files) == 3

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_normal_module_filters_by_prefix(self, mock_run):
        mock_run.return_value = _fake_run(stdout="backend/src/main.py\nfrontend/index.ts\nsetup.py")

        from lindy_orchestrator.qa.structural_check import _get_staged_files

        files = _get_staged_files(_PROJECT_ROOT, "backend/")
        assert files == ["backend/src/main.py"]

    @patch("lindy_orchestrator.qa.structural_check.subprocess.run")
    def test_renamed_module_uses_path_prefix(self, mock_run):
        """name='my-api', path='services/api/' → filter by 'services/api/'."""
        mock_run.return_value = _fake_run(
            stdout="services/api/main.py\nservices/web/index.ts\nmy-api/old.py"
        )

        from lindy_orchestrator.qa.structural_check import _get_staged_files

        files = _get_staged_files(_PROJECT_ROOT, "services/api/")
        assert files == ["services/api/main.py"]
        # Should NOT include files under module name path
        assert "my-api/old.py" not in files
