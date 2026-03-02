"""Tests for config loading."""

from pathlib import Path

import pytest

from lindy_orchestrator.config import OrchestratorConfig, load_config


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_sample_config():
    cfg = load_config(FIXTURES / "sample_config.yaml")

    assert cfg.project.name == "test-project"
    assert cfg.project.branch_prefix == "test"
    assert len(cfg.modules) == 2
    assert cfg.modules[0].name == "backend"
    assert cfg.modules[0].repo == "testorg/test-backend"
    assert cfg.modules[1].name == "frontend"
    assert cfg.planner.mode == "cli"
    assert cfg.safety.max_retries_per_task == 1
    assert cfg.safety.max_parallel == 2


def test_get_module():
    cfg = load_config(FIXTURES / "sample_config.yaml")
    mod = cfg.get_module("backend")
    assert mod.name == "backend"
    assert mod.path == "backend/"


def test_get_module_not_found():
    cfg = load_config(FIXTURES / "sample_config.yaml")
    with pytest.raises(ValueError, match="Unknown module"):
        cfg.get_module("nonexistent")


def test_default_config():
    cfg = OrchestratorConfig()
    assert cfg.project.name == "project"
    assert cfg.project.branch_prefix == "af"
    assert cfg.planner.mode == "cli"
    assert cfg.dispatcher.timeout_seconds == 1800
    assert cfg.safety.max_parallel == 3


def test_config_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_qa_module_none():
    cfg = load_config(FIXTURES / "sample_config.yaml")
    assert cfg.qa_module() is None
