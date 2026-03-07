"""Tests for CLI init and onboard commands."""

from __future__ import annotations


from lindy_orchestrator.cli_init import (
    _detect_modules,
    _detect_tech,
    _generate_config,
    _MODULE_MARKERS,
    _IGNORED_DIRS,
)


class TestDetectModules:
    def test_finds_python_module(self, tmp_path):
        mod = tmp_path / "backend"
        mod.mkdir()
        (mod / "pyproject.toml").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        assert len(result) == 1
        assert result[0] == ("backend", "Python")

    def test_finds_node_module(self, tmp_path):
        mod = tmp_path / "frontend"
        mod.mkdir()
        (mod / "package.json").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        assert len(result) == 1
        assert result[0] == ("frontend", "Node.js")

    def test_finds_multiple_modules(self, tmp_path):
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "pyproject.toml").touch()
        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").touch()
        (tmp_path / "infra").mkdir()
        (tmp_path / "infra" / "go.mod").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        names = [r[0] for r in result]
        assert "backend" in names
        assert "frontend" in names
        assert "infra" in names

    def test_empty_directory(self, tmp_path):
        result = _detect_modules(tmp_path, max_depth=1)
        assert result == []

    def test_ignores_hidden_dirs(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "pyproject.toml").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        assert result == []

    def test_ignores_special_dirs(self, tmp_path):
        for name in ["node_modules", "__pycache__", ".venv"]:
            d = tmp_path / name
            d.mkdir()
            (d / "pyproject.toml").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        assert result == []

    def test_dir_without_markers(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        assert result == []

    def test_sorted_output(self, tmp_path):
        for name in ["zeta", "alpha", "mid"]:
            d = tmp_path / name
            d.mkdir()
            (d / "pyproject.toml").touch()

        result = _detect_modules(tmp_path, max_depth=1)
        names = [r[0] for r in result]
        assert names == sorted(names)


class TestDetectTech:
    def test_python_marker(self, tmp_path):
        (tmp_path / "pyproject.toml").touch()
        assert _detect_tech(tmp_path, 1) == "Python"

    def test_rust_marker(self, tmp_path):
        (tmp_path / "Cargo.toml").touch()
        assert _detect_tech(tmp_path, 1) == "Rust"

    def test_go_marker(self, tmp_path):
        (tmp_path / "go.mod").touch()
        assert _detect_tech(tmp_path, 1) == "Go"

    def test_java_marker(self, tmp_path):
        (tmp_path / "pom.xml").touch()
        assert _detect_tech(tmp_path, 1) == "Java"

    def test_src_directory_fallback(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert _detect_tech(tmp_path, 1) == "source directory"

    def test_no_markers_returns_empty(self, tmp_path):
        (tmp_path / "random.txt").touch()
        assert _detect_tech(tmp_path, 1) == ""

    def test_recursive_depth(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "package.json").touch()

        # depth=1 should not find it in sub
        assert _detect_tech(tmp_path, 1) == ""
        # depth=2 should find it
        assert _detect_tech(tmp_path, 2) == "Node.js"

    def test_first_marker_wins(self, tmp_path):
        # Both markers present, first alphabetically in dict wins
        (tmp_path / "pyproject.toml").touch()
        (tmp_path / "setup.py").touch()
        result = _detect_tech(tmp_path, 1)
        assert result == "Python"


class TestGenerateConfig:
    def test_basic_output(self):
        config = _generate_config("myproject", [("backend", "Python")])
        assert "myproject" in config
        assert "backend" in config
        assert "# tech: Python" in config

    def test_multiple_modules(self):
        modules = [("backend", "Python"), ("frontend", "Node.js")]
        config = _generate_config("proj", modules)
        assert "backend" in config
        assert "frontend" in config

    def test_contains_required_sections(self):
        config = _generate_config("proj", [("mod", "Python")])
        assert "project:" in config
        assert "modules:" in config
        assert "planner:" in config
        assert "dispatcher:" in config
        assert "safety:" in config

    def test_empty_modules(self):
        config = _generate_config("proj", [])
        assert "modules:" in config


class TestModuleMarkers:
    def test_known_markers(self):
        assert "pyproject.toml" in _MODULE_MARKERS
        assert "package.json" in _MODULE_MARKERS
        assert "Cargo.toml" in _MODULE_MARKERS
        assert "go.mod" in _MODULE_MARKERS


class TestIgnoredDirs:
    def test_common_ignored(self):
        assert "node_modules" in _IGNORED_DIRS
        assert "__pycache__" in _IGNORED_DIRS
        assert ".venv" in _IGNORED_DIRS
        assert "dist" in _IGNORED_DIRS
