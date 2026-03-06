"""Tests for QA gate module path resolution.

Covers bugs where QA gates used the module *name* as a filesystem path
instead of the configured module *path*, causing failures when the two
differ (e.g. ``name: my-project, path: ./``).

Scenarios tested:
- Root module (name=lindy-orchestrator, path=./)
- Normal module (name=backend, path=backend/)
- Renamed module (name=my-api, path=services/api/)
- No module_path (backward compat)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lindy_orchestrator.config import CustomGateConfig
from lindy_orchestrator.models import QACheck, QAResult
from lindy_orchestrator.qa import _run_custom_command_gate, run_qa_gate
from lindy_orchestrator.qa.command_check import CommandCheckGate
from lindy_orchestrator.qa.structural_check import _module_file_prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/tmp/fake-project")


def _custom_gate(name: str = "my-pytest", cmd: str = "echo ok", cwd: str = "{module_path}"):
    return CustomGateConfig(name=name, command=cmd, cwd=cwd, timeout=10)


def _fake_run(returncode=0, stdout="ok", stderr=""):
    """Return a mock subprocess result."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# _module_file_prefix: computes git-relative prefix from module config
# ---------------------------------------------------------------------------


class TestModuleFilePrefix:
    """Unit tests for _module_file_prefix helper."""

    def test_root_module_returns_empty(self):
        """path='./' → prefix '' (all files)."""
        prefix = _module_file_prefix(_PROJECT_ROOT, "lindy-orch", str(_PROJECT_ROOT))
        assert prefix == ""

    def test_normal_module_returns_dir_prefix(self):
        """path='backend/' → prefix 'backend/'."""
        prefix = _module_file_prefix(_PROJECT_ROOT, "backend", str(_PROJECT_ROOT / "backend"))
        assert prefix == "backend/"

    def test_renamed_module_uses_path_not_name(self):
        """name='my-api', path='services/api/' → prefix 'services/api/'."""
        prefix = _module_file_prefix(
            _PROJECT_ROOT, "my-api", str(_PROJECT_ROOT / "services" / "api")
        )
        assert prefix == "services/api/"

    def test_no_module_path_falls_back_to_name(self):
        """Backward compat: no module_path → use module_name + '/'."""
        prefix = _module_file_prefix(_PROJECT_ROOT, "backend", None)
        assert prefix == "backend/"

    def test_root_or_star_returns_empty(self):
        """Virtual modules 'root' and '*' always get empty prefix."""
        assert _module_file_prefix(_PROJECT_ROOT, "root", None) == ""
        assert _module_file_prefix(_PROJECT_ROOT, "*", None) == ""

    def test_empty_name_no_path_returns_empty(self):
        assert _module_file_prefix(_PROJECT_ROOT, "", None) == ""


# ---------------------------------------------------------------------------
# _run_custom_command_gate: resolved path vs name-as-path
# ---------------------------------------------------------------------------


class TestCustomGatePathResolution:
    """Regression: module name != module path (e.g. name=lindy-orch, path=./)."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_uses_resolved_path_when_provided(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = _custom_gate()
        resolved = str(_PROJECT_ROOT)

        result = _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "lindy-orchestrator", resolved)

        # cwd passed to subprocess should be the resolved path, NOT root/name
        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == resolved
        assert "lindy-orchestrator" not in actual_cwd
        assert result.passed

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_falls_back_to_name_when_no_resolved_path(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = _custom_gate()

        _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "backend")

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "backend")

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_falls_back_to_root_when_no_module(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = _custom_gate()

        _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "")

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT)


# ---------------------------------------------------------------------------
# _run_custom_command_gate: OSError handling
# ---------------------------------------------------------------------------


class TestCustomGateOSError:
    """Regression: FileNotFoundError from bad cwd crashed the task."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_oserror_returns_failed_result_not_exception(self, mock_run):
        mock_run.side_effect = FileNotFoundError("[Errno 2] No such file or directory: '/bad/path'")
        gate = _custom_gate()

        result = _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "missing")

        assert isinstance(result, QAResult)
        assert not result.passed
        assert "Failed to run command" in result.output
        assert "No such file" in result.output

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_permission_error_returns_failed_result(self, mock_run):
        mock_run.side_effect = PermissionError("Permission denied")
        gate = _custom_gate()

        result = _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "locked")

        assert not result.passed
        assert "Permission denied" in result.output


# ---------------------------------------------------------------------------
# run_qa_gate: module_path passthrough to custom gates
# ---------------------------------------------------------------------------


class TestRunQaGateModulePath:
    """The top-level run_qa_gate should forward module_path to custom gates."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_module_path_forwarded_to_custom_gate(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = _custom_gate(name="my-gate")
        check = QACheck(gate="my-gate", params={})
        resolved = _PROJECT_ROOT  # path: ./ resolves to project root

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            custom_gates=[gate],
            module_path=resolved,
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(resolved)
        assert result.passed

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_without_module_path_uses_name(self, mock_run):
        """Backward compat: no module_path → falls back to root/name."""
        mock_run.return_value = _fake_run()
        gate = _custom_gate(name="my-gate")
        check = QACheck(gate="my-gate", params={})

        run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="backend",
            custom_gates=[gate],
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "backend")


# ---------------------------------------------------------------------------
# run_qa_gate: module_path passthrough to BUILT-IN gates
# ---------------------------------------------------------------------------


class TestRunQaGateBuiltinModulePath:
    """Regression: module_path was forwarded to custom gates but NOT built-in gates.

    Auto-injected command_check gates go through the built-in gate registry,
    so they need module_path too.
    """

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_builtin_command_check_receives_module_path(self, mock_run):
        """Built-in command_check should use resolved module_path for cwd."""
        mock_run.return_value = _fake_run()
        check = QACheck(
            gate="command_check",
            params={"command": "pytest", "cwd": "{module_path}"},
        )

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            module_path=_PROJECT_ROOT,  # root module: path=./
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT)
        assert "lindy-orchestrator" not in actual_cwd
        assert result.passed

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_builtin_command_check_normal_module(self, mock_run):
        """Normal module (name matches path dir): should resolve correctly."""
        mock_run.return_value = _fake_run()
        check = QACheck(
            gate="command_check",
            params={"command": "cargo test", "cwd": "{module_path}"},
        )

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="backend",
            module_path=_PROJECT_ROOT / "backend",
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "backend")
        assert result.passed

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_builtin_command_check_renamed_module(self, mock_run):
        """Renamed module (name='my-api', path='services/api/')."""
        mock_run.return_value = _fake_run()
        check = QACheck(
            gate="command_check",
            params={"command": "npm test", "cwd": "{module_path}"},
        )

        result = run_qa_gate(
            check=check,
            project_root=_PROJECT_ROOT,
            module_name="my-api",
            module_path=_PROJECT_ROOT / "services" / "api",
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "services" / "api")
        assert "my-api" not in actual_cwd
        assert result.passed


# ---------------------------------------------------------------------------
# CommandCheckGate: {module_path} template resolution
# ---------------------------------------------------------------------------


class TestCommandCheckTemplateResolution:
    """Regression: auto-injected command_check with cwd='{module_path}' was
    passed as a literal string instead of being formatted."""

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_module_path_template_is_formatted(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()

        gate.check(
            params={"command": "pytest", "cwd": "{module_path}"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        # Should resolve the template, not contain literal {module_path}
        assert "{module_path}" not in actual_cwd
        assert "backend" in actual_cwd

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_plain_cwd_not_affected(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()

        gate.check(
            params={"command": "pytest", "cwd": "src/"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "src/")

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_template_with_resolved_module_path(self, mock_run):
        """When module_path kwarg is provided, use it for {module_path} template."""
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()

        gate.check(
            params={"command": "pytest", "cwd": "{module_path}"},
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            module_path=str(_PROJECT_ROOT),  # root module
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT)
        assert "lindy-orchestrator" not in actual_cwd

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_no_cwd_param_uses_resolved_module_path(self, mock_run):
        """When no cwd param, use resolved module_path as default."""
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()

        gate.check(
            params={"command": "pytest"},
            project_root=_PROJECT_ROOT,
            module_name="lindy-orchestrator",
            module_path=str(_PROJECT_ROOT),
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT)

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_no_cwd_no_module_path_falls_back_to_name(self, mock_run):
        """Backward compat: no cwd, no module_path → use module_name."""
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()

        gate.check(
            params={"command": "pytest"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )

        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert actual_cwd == str(_PROJECT_ROOT / "backend")


# ---------------------------------------------------------------------------
# CommandCheckGate: OSError handling
# ---------------------------------------------------------------------------


class TestCommandCheckOSError:
    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_oserror_returns_failed_result(self, mock_run):
        mock_run.side_effect = FileNotFoundError("No such directory")
        gate = CommandCheckGate()

        result = gate.check(
            params={"command": "pytest", "cwd": "."},
            project_root=_PROJECT_ROOT,
            module_name="missing",
        )

        assert isinstance(result, QAResult)
        assert not result.passed
        assert "Failed to run command" in result.output


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
