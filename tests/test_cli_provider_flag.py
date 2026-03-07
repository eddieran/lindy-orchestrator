"""Tests for CLI --provider flag parsing and provider selection."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from lindy_orchestrator.cli import app

runner = CliRunner()


class TestCliProviderFlag:
    @patch("lindy_orchestrator.cli.find_codex_cli", return_value=None)
    @patch("lindy_orchestrator.cli._load_cfg")
    def test_codex_provider_checks_codex_cli(self, mock_cfg, mock_codex):
        """--provider codex_cli checks for codex binary, not claude."""
        from lindy_orchestrator.config import OrchestratorConfig

        mock_cfg.return_value = OrchestratorConfig()
        result = runner.invoke(app, ["run", "test goal", "--provider", "codex_cli"])
        assert result.exit_code == 1
        assert "Codex CLI not found" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value=None)
    @patch("lindy_orchestrator.cli._load_cfg")
    def test_default_provider_checks_claude_cli(self, mock_cfg, mock_claude):
        """Without --provider, checks for claude binary."""
        from lindy_orchestrator.config import OrchestratorConfig

        mock_cfg.return_value = OrchestratorConfig()
        result = runner.invoke(app, ["run", "test goal"])
        assert result.exit_code == 1
        assert "Claude CLI not found" in result.output

    @patch("lindy_orchestrator.cli.find_claude_cli", return_value=None)
    @patch("lindy_orchestrator.cli._load_cfg")
    def test_explicit_claude_provider_checks_claude_cli(self, mock_cfg, mock_claude):
        """--provider claude_cli checks for claude binary."""
        from lindy_orchestrator.config import OrchestratorConfig

        mock_cfg.return_value = OrchestratorConfig()
        result = runner.invoke(app, ["run", "test goal", "--provider", "claude_cli"])
        assert result.exit_code == 1
        assert "Claude CLI not found" in result.output

    @patch("lindy_orchestrator.cli._load_cfg")
    def test_provider_flag_overrides_config(self, mock_cfg):
        """--provider flag overrides the config file's dispatcher.provider."""
        from lindy_orchestrator.config import OrchestratorConfig

        cfg = OrchestratorConfig()
        assert cfg.dispatcher.provider == "claude_cli"
        mock_cfg.return_value = cfg

        with patch("lindy_orchestrator.cli.find_codex_cli", return_value=None):
            runner.invoke(app, ["run", "test goal", "--provider", "codex_cli"])
            # After the CLI processes --provider, the config should be updated
            assert cfg.dispatcher.provider == "codex_cli"
