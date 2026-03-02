"""Tests for the static project analyzer."""

import json

from lindy_orchestrator.discovery.analyzer import analyze_project


def test_detect_python_module(tmp_path):
    """Detects a Python module from pyproject.toml."""
    mod = tmp_path / "backend"
    mod.mkdir()
    (mod / "pyproject.toml").write_text(
        '[project]\nname = "backend"\ndependencies = [\n  "fastapi>=0.100",\n  "pydantic>=2.0",\n]\n'
    )
    (mod / "src").mkdir()
    (mod / "src" / "main.py").write_text("print('hello')")
    (mod / "tests").mkdir()
    (mod / "conftest.py").write_text("")

    profile = analyze_project(tmp_path)

    assert len(profile.modules) == 1
    m = profile.modules[0]
    assert m.name == "backend"
    assert "Python" in m.tech_stack
    assert "fastapi" in m.dependencies
    assert "pytest" in " ".join(m.test_commands)
    assert m.entry_points  # src/main.py detected


def test_detect_node_module(tmp_path):
    """Detects a Node.js module from package.json."""
    mod = tmp_path / "frontend"
    mod.mkdir()
    pkg = {
        "name": "frontend",
        "scripts": {"test": "vitest run", "build": "vite build", "lint": "eslint ."},
        "dependencies": {"react": "^18", "next": "^14"},
        "devDependencies": {"typescript": "^5", "vitest": "^1"},
    }
    (mod / "package.json").write_text(json.dumps(pkg))
    (mod / "src").mkdir()
    (mod / "src" / "index.ts").write_text("")

    profile = analyze_project(tmp_path)

    assert len(profile.modules) == 1
    m = profile.modules[0]
    assert "Node.js" in m.tech_stack
    assert "React" in m.tech_stack
    assert "react" in m.dependencies
    assert any("test" in c for c in m.test_commands)
    assert any("build" in c for c in m.build_commands)
    assert any("lint" in c for c in m.lint_commands)


def test_detect_multi_module_monorepo(tmp_path):
    """Detects multiple modules = monorepo."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[project]\nname='be'\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text('{"name":"fe"}')

    profile = analyze_project(tmp_path)

    assert len(profile.modules) == 2
    assert profile.monorepo is True
    names = {m.name for m in profile.modules}
    assert names == {"backend", "frontend"}


def test_detect_rust_module(tmp_path):
    """Detects a Rust module from Cargo.toml."""
    mod = tmp_path / "engine"
    mod.mkdir()
    (mod / "Cargo.toml").write_text(
        '[package]\nname = "engine"\n[dependencies]\ntokio = "1.0"\nserde = "1.0"\n'
    )
    (mod / "src").mkdir()
    (mod / "src" / "main.rs").write_text("fn main() {}")

    profile = analyze_project(tmp_path)

    assert len(profile.modules) == 1
    m = profile.modules[0]
    assert "Rust" in m.tech_stack
    assert "tokio" in m.dependencies
    assert "cargo test" in " ".join(m.test_commands)


def test_dir_tree_generated(tmp_path):
    """Modules get a directory tree."""
    mod = tmp_path / "svc"
    mod.mkdir()
    (mod / "pyproject.toml").write_text("[project]\nname='svc'\n")
    (mod / "src").mkdir()
    (mod / "src" / "app.py").write_text("")
    (mod / "tests").mkdir()
    (mod / "tests" / "test_app.py").write_text("")

    profile = analyze_project(tmp_path)
    m = profile.modules[0]

    assert "src/" in m.dir_tree
    assert "tests/" in m.dir_tree


def test_cross_module_files_detected(tmp_path):
    """Detects shared root-level files."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "pyproject.toml").write_text("[project]\nname='be'\n")
    (tmp_path / "docker-compose.yml").write_text("version: '3'")
    (tmp_path / "README.md").write_text("# Project")

    profile = analyze_project(tmp_path)

    assert "docker-compose.yml" in profile.cross_module_files
    assert "README.md" in profile.cross_module_files


def test_empty_dir_returns_empty_profile(tmp_path):
    """An empty directory produces an empty profile."""
    profile = analyze_project(tmp_path)
    assert len(profile.modules) == 0
    assert profile.monorepo is False


def test_ci_detection_github_actions(tmp_path):
    """Detects GitHub Actions CI."""
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "pyproject.toml").write_text("[project]\nname='svc'\n")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI")

    profile = analyze_project(tmp_path)
    assert "GitHub Actions" in profile.detected_ci
