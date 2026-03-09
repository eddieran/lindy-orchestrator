"""Tests for global config (~/.lindy/config.yaml)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from lindy_orchestrator.cli import app
from lindy_orchestrator.config import (
    GlobalConfig,
    load_global_config,
    save_global_config,
)


# ---------------------------------------------------------------------------
# GlobalConfig model
# ---------------------------------------------------------------------------


class TestGlobalConfig:
    def test_default_provider(self):
        cfg = GlobalConfig()
        assert cfg.provider == "claude_cli"

    def test_valid_codex_provider(self):
        cfg = GlobalConfig(provider="codex_cli")
        assert cfg.provider == "codex_cli"

    def test_invalid_provider_raises(self):
        with pytest.raises(Exception):
            GlobalConfig(provider="unknown_provider")


# ---------------------------------------------------------------------------
# load_global_config
# ---------------------------------------------------------------------------


class TestLoadGlobalConfig:
    def test_returns_defaults_when_file_missing(self, tmp_path):
        missing = tmp_path / "config.yaml"
        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", missing):
            cfg = load_global_config()
        assert cfg.provider == "claude_cli"

    def test_loads_provider_from_file(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("provider: codex_cli\n")
        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", cfg_file):
            cfg = load_global_config()
        assert cfg.provider == "codex_cli"

    def test_returns_defaults_on_corrupt_file(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("provider: bad_provider\n")
        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", cfg_file):
            cfg = load_global_config()
        # Invalid provider → exception caught → defaults returned
        assert cfg.provider == "claude_cli"


# ---------------------------------------------------------------------------
# save_global_config
# ---------------------------------------------------------------------------


class TestSaveGlobalConfig:
    def test_saves_and_reloads(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_dir = tmp_path

        with (
            patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", cfg_file),
            patch("lindy_orchestrator.config.GLOBAL_CONFIG_DIR", cfg_dir),
        ):
            save_global_config(GlobalConfig(provider="codex_cli"))
            loaded = load_global_config()

        assert loaded.provider == "codex_cli"
        raw = yaml.safe_load(cfg_file.read_text())
        assert raw["provider"] == "codex_cli"


# ---------------------------------------------------------------------------
# CLI: config show / config set
# ---------------------------------------------------------------------------


runner = CliRunner()


class TestConfigCli:
    def test_config_show_default(self, tmp_path):
        missing = tmp_path / "config.yaml"
        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", missing):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "claude_cli" in result.output

    def test_config_show_codex(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("provider: codex_cli\n")
        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", cfg_file):
            result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "codex_cli" in result.output

    def test_config_set_provider(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        with (
            patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", cfg_file),
            patch("lindy_orchestrator.config.GLOBAL_CONFIG_DIR", tmp_path),
            patch("lindy_orchestrator.cli_config.GLOBAL_CONFIG_PATH", cfg_file),
        ):
            result = runner.invoke(app, ["config", "set", "provider", "codex_cli"])
        assert result.exit_code == 0
        assert "codex_cli" in result.output

    def test_config_set_invalid_provider(self, tmp_path):
        result = runner.invoke(app, ["config", "set", "provider", "bad_provider"])
        assert result.exit_code != 0

    def test_config_set_unknown_key(self, tmp_path):
        result = runner.invoke(app, ["config", "set", "unknown_key", "value"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# load_config respects global config
# ---------------------------------------------------------------------------


class TestLoadConfigMergesGlobal:
    def test_global_provider_applied_when_not_in_yaml(self, tmp_path):
        """When orchestrator.yaml has no dispatcher.provider, use global config."""
        yaml_file = tmp_path / "orchestrator.yaml"
        yaml_file.write_text("project:\n  name: test\nmodules: []\n")

        global_cfg_file = tmp_path / "global_config.yaml"
        global_cfg_file.write_text("provider: codex_cli\n")

        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", global_cfg_file):
            from lindy_orchestrator.config import load_config

            cfg = load_config(yaml_file)

        assert cfg.dispatcher.provider == "codex_cli"

    def test_yaml_provider_overrides_global(self, tmp_path):
        """When orchestrator.yaml explicitly sets dispatcher.provider, it wins."""
        yaml_file = tmp_path / "orchestrator.yaml"
        yaml_file.write_text(
            "project:\n  name: test\nmodules: []\ndispatcher:\n  provider: claude_cli\n"
        )

        global_cfg_file = tmp_path / "global_config.yaml"
        global_cfg_file.write_text("provider: codex_cli\n")

        with patch("lindy_orchestrator.config.GLOBAL_CONFIG_PATH", global_cfg_file):
            from lindy_orchestrator.config import load_config

            cfg = load_config(yaml_file)

        assert cfg.dispatcher.provider == "claude_cli"
