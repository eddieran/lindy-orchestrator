"""Tests for QA gate module path resolution.

Covers the bug where custom QA gates used the module *name* as a filesystem
path instead of the configured module *path*, causing failures when the two
differ (e.g. ``name: my-project, path: ./``).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.config import CustomGateConfig
from lindy_orchestrator.models import QACheck, QAResult
from lindy_orchestrator.qa import _run_custom_command_gate, run_qa_gate
from lindy_orchestrator.qa.command_check import CommandCheckGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path("/tmp/fake-project")


def _custom_gate(name: str = "my-pytest", cmd: str = "echo ok", cwd: str = "{module_path}"):
    return CustomGateConfig(name=name, command=cmd, cwd=cwd, timeout=10)


def _fake_run(returncode=0, stdout="ok", stderr=""):
    """Return a mock subprocess result."""
    from unittest.mock import MagicMock

    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


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
# run_qa_gate: module_path passthrough
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
