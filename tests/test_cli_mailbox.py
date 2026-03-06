"""Tests for the mailbox CLI command."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.mailbox import Mailbox, Message

runner = CliRunner()


def _setup_project(tmp_path: Path, mailbox_enabled: bool = True) -> Path:
    """Create a minimal project with orchestrator.yaml."""
    config = {
        "project": {"name": "test-project", "branch_prefix": "af"},
        "modules": [
            {"name": "backend", "path": "backend/"},
            {"name": "frontend", "path": "frontend/"},
        ],
        "mailbox": {
            "enabled": mailbox_enabled,
            "dir": ".orchestrator/mailbox",
        },
    }
    config_path = tmp_path / "orchestrator.yaml"
    config_path.write_text(yaml.dump(config))

    # Create module dirs
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / ".orchestrator" / "mailbox").mkdir(parents=True)

    return tmp_path


class TestMailboxCLI:
    def test_mailbox_disabled_warning(self, tmp_path):
        project = _setup_project(tmp_path, mailbox_enabled=False)
        result = runner.invoke(app, ["mailbox", "-c", str(project / "orchestrator.yaml")])
        assert "disabled" in result.output.lower()

    def test_mailbox_summary_empty(self, tmp_path):
        project = _setup_project(tmp_path)
        result = runner.invoke(app, ["mailbox", "-c", str(project / "orchestrator.yaml")])
        assert result.exit_code == 0
        assert "no pending messages" in result.output.lower() or "Mailbox Summary" in result.output

    def test_mailbox_send_message(self, tmp_path):
        project = _setup_project(tmp_path)
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "--send-from",
                "frontend",
                "-m",
                "Need API endpoint",
                "-c",
                str(project / "orchestrator.yaml"),
            ],
        )
        assert result.exit_code == 0
        assert "Sent" in result.output

        # Verify message was persisted
        mb = Mailbox(project / ".orchestrator" / "mailbox")
        messages = mb.receive("backend")
        assert len(messages) == 1
        assert messages[0].content == "Need API endpoint"

    def test_mailbox_send_requires_message(self, tmp_path):
        project = _setup_project(tmp_path)
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "-c",
                str(project / "orchestrator.yaml"),
            ],
        )
        assert result.exit_code == 1

    def test_mailbox_view_module(self, tmp_path):
        project = _setup_project(tmp_path)
        mb = Mailbox(project / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="frontend", to_module="backend", content="Ready to integrate"))

        result = runner.invoke(
            app,
            ["mailbox", "backend", "-c", str(project / "orchestrator.yaml")],
        )
        assert result.exit_code == 0
        assert "Ready to integrate" in result.output
        assert "frontend" in result.output

    def test_mailbox_view_empty_module(self, tmp_path):
        project = _setup_project(tmp_path)
        result = runner.invoke(
            app,
            ["mailbox", "backend", "-c", str(project / "orchestrator.yaml")],
        )
        assert result.exit_code == 0
        assert "no pending" in result.output.lower()

    def test_mailbox_view_json(self, tmp_path):
        project = _setup_project(tmp_path)
        mb = Mailbox(project / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="a", to_module="backend", content="test json"))

        result = runner.invoke(
            app,
            ["mailbox", "backend", "--json", "-c", str(project / "orchestrator.yaml")],
        )
        assert result.exit_code == 0
        assert "test json" in result.output

    def test_mailbox_send_default_from(self, tmp_path):
        project = _setup_project(tmp_path)
        result = runner.invoke(
            app,
            [
                "mailbox",
                "--send-to",
                "backend",
                "-m",
                "From CLI",
                "-c",
                str(project / "orchestrator.yaml"),
            ],
        )
        assert result.exit_code == 0

        mb = Mailbox(project / ".orchestrator" / "mailbox")
        messages = mb.receive("backend")
        assert messages[0].from_module == "cli"

    def test_mailbox_summary_with_messages(self, tmp_path):
        project = _setup_project(tmp_path)
        mb = Mailbox(project / ".orchestrator" / "mailbox")
        mb.send(Message(from_module="a", to_module="backend", content="msg1"))
        mb.send(Message(from_module="b", to_module="backend", content="msg2"))

        result = runner.invoke(app, ["mailbox", "-c", str(project / "orchestrator.yaml")])
        assert result.exit_code == 0
        assert "2 pending" in result.output
