"""Tests for config loading."""

from pathlib import Path

import pytest
import yaml

from lindy_orchestrator.config import OrchestratorConfig, QAGatesConfig, load_config


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
    assert cfg.safety.max_parallel == 10
    assert cfg.mailbox.enabled is True


def test_config_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.yaml")


def test_qa_module_none():
    cfg = load_config(FIXTURES / "sample_config.yaml")
    assert cfg.qa_module() is None


def test_root_module_path():
    """module_path('root') returns the project root."""
    cfg = load_config(FIXTURES / "sample_config.yaml")
    root_path = cfg.module_path("root")
    assert root_path == cfg.root.resolve()


def test_root_get_module():
    """get_module('root') returns a virtual ModuleConfig."""
    cfg = load_config(FIXTURES / "sample_config.yaml")
    mod = cfg.get_module("root")
    assert mod.name == "root"
    assert mod.path == "."


def test_star_module_path():
    """module_path('*') also returns project root."""
    cfg = load_config(FIXTURES / "sample_config.yaml")
    assert cfg.module_path("*") == cfg.root.resolve()


# ---------------------------------------------------------------------------
# Module-scoped qa_gates normalization
# ---------------------------------------------------------------------------


class TestNormalizeQaGates:
    def test_module_scoped_gates_become_custom(self):
        """qa_gates.backend: [...] is converted to custom entries."""
        qa = QAGatesConfig.model_validate(
            {
                "backend": [
                    {"name": "pytest", "command": "cd backend && pytest"},
                ],
                "frontend": [
                    {"name": "playwright", "command": "npx playwright test"},
                ],
            }
        )
        assert len(qa.custom) == 2
        assert qa.custom[0].modules == ["backend"]
        assert qa.custom[1].modules == ["frontend"]
        assert qa.custom[0].cwd == "."
        assert qa.custom[1].cwd == "."

    def test_existing_custom_preserved(self):
        """Existing custom gates are preserved alongside module-scoped ones."""
        qa = QAGatesConfig.model_validate(
            {
                "custom": [
                    {"name": "global-lint", "command": "ruff check ."},
                ],
                "backend": [
                    {"name": "pytest", "command": "pytest"},
                ],
            }
        )
        assert len(qa.custom) == 2
        assert qa.custom[0].name == "global-lint"
        assert qa.custom[1].name == "pytest"

    def test_known_keys_not_normalized(self):
        """ci_check, structural, layer_check are not treated as modules."""
        qa = QAGatesConfig.model_validate(
            {
                "structural": {"max_file_lines": 300},
                "layer_check": {"enabled": True},
            }
        )
        assert len(qa.custom) == 0
        assert qa.structural.max_file_lines == 300

    def test_no_data_produces_defaults(self):
        qa = QAGatesConfig.model_validate({})
        assert len(qa.custom) == 0

    def test_module_scoped_gate_with_explicit_cwd(self):
        """Explicit cwd in a module-scoped gate is preserved."""
        qa = QAGatesConfig.model_validate(
            {
                "backend": [
                    {"name": "test", "command": "pytest", "cwd": "backend/"},
                ],
            }
        )
        assert qa.custom[0].cwd == "backend/"

    def test_load_module_scoped_config(self, tmp_path):
        """Full round-trip: module-scoped YAML → loaded config."""
        config_data = {
            "project": {"name": "test"},
            "modules": [{"name": "backend", "path": "backend/"}],
            "qa_gates": {
                "backend": [
                    {
                        "name": "pytest",
                        "command": "cd backend && python -m pytest tests/",
                    }
                ],
            },
        }
        (tmp_path / ".orchestrator").mkdir(parents=True, exist_ok=True)
        config_file = tmp_path / ".orchestrator" / "config.yaml"
        config_file.write_text(yaml.dump(config_data))
        cfg = load_config(config_file)
        assert len(cfg.qa_gates.custom) == 1
        assert cfg.qa_gates.custom[0].name == "pytest"
        assert cfg.qa_gates.custom[0].modules == ["backend"]
        assert cfg.qa_gates.custom[0].cwd == "."
