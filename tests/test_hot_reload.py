"""Tests for config hot-reload."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from lindy_orchestrator.config import OrchestratorConfig, load_config


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data))


class TestCheckReload:
    def test_returns_none_when_unchanged(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "orchestrator.yaml"
        _write_config(cfg_path, {"project": {"name": "test"}})
        cfg = load_config(cfg_path)
        assert cfg.check_reload() is None

    def test_returns_config_when_mtime_changed(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "orchestrator.yaml"
        _write_config(cfg_path, {"safety": {"max_retries_per_task": 2}})
        cfg = load_config(cfg_path)

        # Touch the file to advance mtime
        time.sleep(0.05)
        _write_config(cfg_path, {"safety": {"max_retries_per_task": 5}})

        result = cfg.check_reload()
        assert result is cfg  # returns self
        assert cfg.safety.max_retries_per_task == 5

    def test_only_updates_safe_sections(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "orchestrator.yaml"
        _write_config(
            cfg_path,
            {
                "modules": [{"name": "api", "path": "api/"}],
                "safety": {"max_retries_per_task": 1},
            },
        )
        cfg = load_config(cfg_path)
        assert len(cfg.modules) == 1
        assert cfg.modules[0].name == "api"

        time.sleep(0.05)
        _write_config(
            cfg_path,
            {
                "modules": [{"name": "web", "path": "web/"}, {"name": "api", "path": "api/"}],
                "safety": {"max_retries_per_task": 10},
            },
        )
        cfg.check_reload()

        # Safety updated
        assert cfg.safety.max_retries_per_task == 10
        # Modules NOT updated (unsafe mid-run)
        assert len(cfg.modules) == 1
        assert cfg.modules[0].name == "api"

    def test_handles_missing_file(self, tmp_path: Path) -> None:
        cfg = OrchestratorConfig()
        cfg._config_path = tmp_path / "nonexistent.yaml"
        cfg._config_mtime = 0.0
        assert cfg.check_reload() is None

    def test_handles_invalid_file(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "orchestrator.yaml"
        _write_config(cfg_path, {"safety": {"max_retries_per_task": 2}})
        cfg = load_config(cfg_path)

        time.sleep(0.05)
        cfg_path.write_text("{{invalid yaml: [")

        assert cfg.check_reload() is None
        # Original value preserved
        assert cfg.safety.max_retries_per_task == 2

    def test_no_config_path_returns_none(self) -> None:
        cfg = OrchestratorConfig()
        assert cfg._config_path is None
        assert cfg.check_reload() is None
