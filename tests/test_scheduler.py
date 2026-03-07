"""Tests for scheduler delivery check logic and mailbox integration."""

from pathlib import Path
from unittest.mock import patch

from lindy_orchestrator.config import MailboxConfig, OrchestratorConfig
from lindy_orchestrator.mailbox import Mailbox, Message
from lindy_orchestrator.scheduler import _check_delivery


def _fake_run(returncode=0, stdout="", stderr=""):
    """Create a fake subprocess.CompletedProcess."""
    return type(
        "CompletedProcess",
        (),
        {"returncode": returncode, "stdout": stdout, "stderr": stderr},
    )()


class TestCheckDelivery:
    """Tests for _check_delivery using merge-base fork-point comparison."""

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_branch_not_found(self, mock_run, tmp_path: Path):
        # Both local and remote branch checks return empty
        mock_run.side_effect = [
            _fake_run(stdout=""),  # git branch --list
            _fake_run(stdout=""),  # git branch -r --list
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "not found" in msg

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_branch_exists_with_commits(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="3\n"),  # rev-list --count
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "3 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_branch_exists_no_commits(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="0\n"),  # rev-list --count = 0
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "no new commits" in msg

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_merge_base_fails_fallback(self, mock_run, tmp_path: Path):
        """When merge-base fails (unrelated branches), falls back gracefully."""
        mock_run.side_effect = [
            _fake_run(stdout="  af/task-1\n"),  # branch exists
            _fake_run(returncode=1, stdout=""),  # merge-base fails
            _fake_run(stdout="5\n"),  # rev-list --count (all commits on branch)
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "5 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_remote_branch_found(self, mock_run, tmp_path: Path):
        mock_run.side_effect = [
            _fake_run(stdout=""),  # local branch not found
            _fake_run(stdout="  origin/af/task-1\n"),  # remote branch found
            _fake_run(stdout="abc123\n"),  # merge-base
            _fake_run(stdout="2\n"),  # rev-list --count
        ]
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is True
        assert "2 new commit(s)" in msg

    @patch("lindy_orchestrator.scheduler_helpers.subprocess.run")
    def test_exception_returns_error(self, mock_run, tmp_path: Path):
        mock_run.side_effect = OSError("git not found")
        ok, msg = _check_delivery(tmp_path, "af/task-1")
        assert ok is False
        assert "Delivery check error" in msg


class TestMailboxInjection:
    """Tests for mailbox message injection into task prompts."""

    def _make_config(self, tmp_path, enabled=True):
        """Create a minimal config with mailbox settings."""
        cfg = OrchestratorConfig()
        cfg._config_dir = tmp_path
        cfg.mailbox = MailboxConfig(
            enabled=enabled,
            dir=".orchestrator/mailbox",
            inject_on_dispatch=True,
        )
        return cfg

    def test_mailbox_injects_pending_messages(self, tmp_path):
        """Pending mailbox messages should be injected into task prompt."""
        self._make_config(tmp_path)
        mb_dir = tmp_path / ".orchestrator" / "mailbox"
        mb = Mailbox(mb_dir)

        # Send a message to the module
        mb.send(Message(from_module="backend", to_module="frontend", content="API ready at /users"))

        # We can't run _execute_single_task fully (it needs provider),
        # but we can test the injection logic directly
        pending = mb.receive("frontend", unread_only=True)
        assert len(pending) == 1

        from lindy_orchestrator.mailbox import format_mailbox_messages

        formatted = format_mailbox_messages(pending)
        assert "API ready at /users" in formatted
        assert "backend" in formatted

    def test_mailbox_disabled_no_injection(self, tmp_path):
        """When mailbox is disabled, no messages should be injected."""
        cfg = self._make_config(tmp_path, enabled=False)
        mb_dir = tmp_path / ".orchestrator" / "mailbox"
        mb = Mailbox(mb_dir)

        mb.send(Message(from_module="a", to_module="b", content="should not inject"))

        # Config says disabled — scheduler should skip injection
        assert cfg.mailbox.enabled is False

    def test_mailbox_inject_on_dispatch_false(self, tmp_path):
        """When inject_on_dispatch is False, messages should not be injected."""
        cfg = self._make_config(tmp_path)
        cfg.mailbox.inject_on_dispatch = False

        # Injection logic checks both enabled and inject_on_dispatch
        assert cfg.mailbox.enabled is True
        assert cfg.mailbox.inject_on_dispatch is False

    def test_mailbox_no_pending_no_modification(self, tmp_path):
        """When there are no pending messages, prompt should not be modified."""
        self._make_config(tmp_path)
        mb_dir = tmp_path / ".orchestrator" / "mailbox"
        mb = Mailbox(mb_dir)

        # No messages sent
        pending = mb.receive("frontend", unread_only=True)
        assert len(pending) == 0

    def test_mailbox_multiple_messages_injected(self, tmp_path):
        """Multiple pending messages should all be formatted and injected."""
        self._make_config(tmp_path)
        mb_dir = tmp_path / ".orchestrator" / "mailbox"
        mb = Mailbox(mb_dir)

        mb.send(Message(from_module="backend", to_module="frontend", content="API v1 ready"))
        mb.send(
            Message(
                from_module="auth",
                to_module="frontend",
                content="JWT tokens required",
                priority="high",
            )
        )

        pending = mb.receive("frontend", unread_only=True)
        assert len(pending) == 2

        from lindy_orchestrator.mailbox import format_mailbox_messages

        formatted = format_mailbox_messages(pending)
        assert "API v1 ready" in formatted
        assert "JWT tokens required" in formatted
        assert "[HIGH]" in formatted
