"""Security tests for audit task-6 fixes.

Covers:
- H-01: Command injection via shell=True in custom QA gates
- M-01: shell=True in CommandCheckGate
- M-28: str.format() attribute access in QA path substitution
- M-30: Path traversal via session_id
- M-31: Path traversal via mailbox module name
- M-05: _delete_branch returncode check
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lindy_orchestrator.config import CustomGateConfig
from lindy_orchestrator.gc import _delete_branch
from lindy_orchestrator.mailbox import Mailbox, Message
from lindy_orchestrator.qa import _run_custom_command_gate, _validate_path_for_substitution
from lindy_orchestrator.qa.command_check import CommandCheckGate
from lindy_orchestrator.session import SessionManager

_PROJECT_ROOT = Path("/tmp/fake-project")


def _fake_run(returncode=0, stdout="ok", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _custom_gate(name="test-gate", cmd="echo ok", cwd="{module_path}"):
    return CustomGateConfig(name=name, command=cmd, cwd=cwd, timeout=10)


# ---------------------------------------------------------------------------
# H-01: shell=True command injection in qa/__init__.py
# ---------------------------------------------------------------------------


class TestCustomGateShellInjection:
    """H-01: _run_custom_command_gate must not use shell=True."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_uses_shell_false(self, mock_run):
        """subprocess.run must be called with shell=False (no shell kwarg)."""
        mock_run.return_value = _fake_run()
        gate = _custom_gate()
        _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "backend")
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("shell") is not True

    def test_rejects_shell_metachar_in_module_path(self):
        """Module paths with shell metacharacters must be rejected."""
        gate = _custom_gate()
        result = _run_custom_command_gate(
            gate, {}, _PROJECT_ROOT, "backend; rm -rf /", None
        )
        assert not result.passed
        assert "Unsafe" in result.output or "path_validation_failed" in str(result.details)

    def test_rejects_pipe_in_module_path(self):
        gate = _custom_gate()
        result = _run_custom_command_gate(
            gate, {}, _PROJECT_ROOT, "x | cat /etc/passwd", None
        )
        assert not result.passed

    def test_rejects_dollar_subshell_in_module_path(self):
        gate = _custom_gate()
        result = _run_custom_command_gate(
            gate, {}, _PROJECT_ROOT, "$(whoami)", None
        )
        assert not result.passed

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_safe_path_passes(self, mock_run):
        """Normal paths should work fine."""
        mock_run.return_value = _fake_run()
        gate = _custom_gate()
        result = _run_custom_command_gate(gate, {}, _PROJECT_ROOT, "backend")
        assert result.passed


# ---------------------------------------------------------------------------
# M-28: str.format() attribute access prevention
# ---------------------------------------------------------------------------


class TestFormatStringInjection:
    """M-28: str.replace() must be used instead of str.format()."""

    @patch("lindy_orchestrator.qa.subprocess.run")
    def test_format_attribute_access_neutralized(self, mock_run):
        """A module_path like {module_path.__class__} should not expose internals."""
        mock_run.return_value = _fake_run()
        # A gate with a command that tries Python format attribute access
        gate = _custom_gate(cmd="echo {module_path}")
        # Since we validate the path itself, this would be caught by path validation.
        # But if it somehow passes, str.replace only substitutes exact matches.
        result = _run_custom_command_gate(
            gate, {}, _PROJECT_ROOT, "backend"
        )
        # The command should contain the literal path, not class info
        if result.passed:
            cmd_args = mock_run.call_args[0][0]
            combined = " ".join(cmd_args)
            assert "__class__" not in combined


class TestPathValidation:
    """Validation helper for module path substitution."""

    def test_safe_path_accepted(self):
        assert _validate_path_for_substitution("/usr/local/project") is True

    def test_safe_path_with_dots_accepted(self):
        assert _validate_path_for_substitution("./backend") is True

    def test_path_with_semicolon_rejected(self):
        assert _validate_path_for_substitution("/tmp/x; rm -rf /") is False

    def test_path_with_pipe_rejected(self):
        assert _validate_path_for_substitution("/tmp/x | cat") is False

    def test_path_with_backtick_rejected(self):
        assert _validate_path_for_substitution("/tmp/`whoami`") is False

    def test_path_with_dollar_rejected(self):
        assert _validate_path_for_substitution("/tmp/$(id)") is False

    def test_path_with_spaces_rejected(self):
        assert _validate_path_for_substitution("/tmp/my dir") is False


# ---------------------------------------------------------------------------
# M-01: shell=True in CommandCheckGate
# ---------------------------------------------------------------------------


class TestCommandCheckShellInjection:
    """M-01: CommandCheckGate must use shlex.split + shell=False for str commands."""

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_string_command_uses_shell_false(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()
        gate.check(
            params={"command": "pytest tests/"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )
        call_kwargs = mock_run.call_args.kwargs
        assert "shell" not in call_kwargs or call_kwargs["shell"] is not True

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_string_command_split_correctly(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()
        gate.check(
            params={"command": "pytest tests/ -v --tb=short"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args == ["pytest", "tests/", "-v", "--tb=short"]

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_list_command_passed_directly(self, mock_run):
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()
        gate.check(
            params={"command": ["pytest", "tests/"]},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )
        cmd_args = mock_run.call_args[0][0]
        assert cmd_args == ["pytest", "tests/"]

    def test_malformed_command_string_returns_failure(self):
        """Unparseable shlex input should fail gracefully."""
        gate = CommandCheckGate()
        result = gate.check(
            params={"command": "echo 'unterminated"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )
        assert not result.passed
        assert "Failed to parse command" in result.output

    @patch("lindy_orchestrator.qa.command_check.subprocess.run")
    def test_cwd_uses_replace_not_format(self, mock_run):
        """M-28: cwd template should use str.replace, not str.format."""
        mock_run.return_value = _fake_run()
        gate = CommandCheckGate()
        gate.check(
            params={"command": "pytest", "cwd": "{module_path}"},
            project_root=_PROJECT_ROOT,
            module_name="backend",
        )
        actual_cwd = mock_run.call_args.kwargs.get("cwd") or mock_run.call_args[1].get("cwd")
        assert "{module_path}" not in actual_cwd


# ---------------------------------------------------------------------------
# M-30: Path traversal via session_id
# ---------------------------------------------------------------------------


class TestSessionPathTraversal:
    """M-30: SessionManager.load must reject traversal in session_id."""

    def test_rejects_path_traversal_session_id(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        result = mgr.load("../../etc/passwd")
        assert result is None

    def test_rejects_slash_in_session_id(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        result = mgr.load("foo/bar")
        assert result is None

    def test_rejects_dot_dot_session_id(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        result = mgr.load("..%2f..%2fetc%2fpasswd")
        assert result is None

    def test_normal_session_id_works(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        session = mgr.create(goal="test")
        loaded = mgr.load(session.session_id)
        assert loaded is not None
        assert loaded.goal == "test"

    def test_alphanumeric_dash_underscore_accepted(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions")
        mgr.create(goal="test")
        # Valid format but doesn't exist — should return None (not raise)
        assert mgr.load("abc-def_123") is None


# ---------------------------------------------------------------------------
# M-31: Path traversal via mailbox module name
# ---------------------------------------------------------------------------


class TestMailboxPathTraversal:
    """M-31: Mailbox._inbox_path must reject traversal in module name."""

    def test_rejects_path_traversal_module(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        with pytest.raises(ValueError, match="Unsafe module name"):
            mb._inbox_path("../../etc/passwd")

    def test_rejects_slash_in_module(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        with pytest.raises(ValueError, match="Unsafe module name"):
            mb._inbox_path("foo/bar")

    def test_normal_module_name_works(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        path = mb._inbox_path("backend")
        assert path.name == "backend.jsonl"
        assert path.parent == (tmp_path / "mailbox")

    def test_module_with_dash_and_underscore(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        path = mb._inbox_path("my-api_v2")
        assert path.name == "my-api_v2.jsonl"

    def test_module_with_dot(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        path = mb._inbox_path("my.module")
        assert path.name == "my.module.jsonl"

    def test_send_rejects_traversal_in_to_module(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        msg = Message(from_module="a", to_module="../etc/passwd", content="test")
        with pytest.raises(ValueError, match="Unsafe module name"):
            mb.send(msg)

    def test_receive_rejects_traversal(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        with pytest.raises(ValueError, match="Unsafe module name"):
            mb.receive("../../secret")


# ---------------------------------------------------------------------------
# M-05: _delete_branch returncode check
# ---------------------------------------------------------------------------


class TestDeleteBranchReturnCode:
    """M-05: _delete_branch should return success/failure status."""

    @patch("lindy_orchestrator.gc.subprocess.run")
    def test_returns_true_on_success(self, mock_run):
        mock_run.return_value = _fake_run(returncode=0)
        assert _delete_branch(_PROJECT_ROOT, "af/task-old") is True

    @patch("lindy_orchestrator.gc.subprocess.run")
    def test_returns_false_on_failure(self, mock_run):
        mock_run.return_value = _fake_run(returncode=1, stderr="error: branch not found")
        assert _delete_branch(_PROJECT_ROOT, "nonexistent") is False


# ---------------------------------------------------------------------------
# No yaml.load, eval, exec in src/
# ---------------------------------------------------------------------------


class TestNoUnsafePatterns:
    """Verify no unsafe patterns remain in source code."""

    def test_no_shell_true_in_qa(self):
        """No shell=True should exist as code in qa/__init__.py."""
        qa_init = Path("src/lindy_orchestrator/qa/__init__.py").read_text()
        for line in qa_init.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "shell=True" in stripped:
                pytest.fail(f"shell=True found in code: {stripped}")

    def test_no_shell_true_in_command_check(self):
        cmd_check = Path("src/lindy_orchestrator/qa/command_check.py").read_text()
        for line in cmd_check.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "shell=True" in stripped:
                pytest.fail(f"shell=True found in code: {stripped}")

    def test_yaml_safe_load_only(self):
        config_src = Path("src/lindy_orchestrator/config.py").read_text()
        assert "yaml.safe_load" in config_src
        # Ensure no bare yaml.load (would be yaml.load without safe_load)
        lines = config_src.splitlines()
        for line in lines:
            if "yaml.load(" in line and "safe_load" not in line:
                pytest.fail(f"Unsafe yaml.load found: {line}")
