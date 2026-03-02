"""Tests for the artifact generator."""

from lindy_orchestrator.discovery.generator import generate_artifacts
from lindy_orchestrator.models import CrossModuleDep, DiscoveryContext, ModuleProfile


def _make_ctx(tmp_path, **kwargs):
    defaults = {
        "project_name": "test-project",
        "project_description": "A test project for testing",
        "root": str(tmp_path),
        "modules": [
            ModuleProfile(
                name="backend",
                path="backend",
                tech_stack=["Python", "FastAPI"],
                detected_patterns=["REST API"],
                test_commands=["pytest"],
                lint_commands=["ruff check ."],
                dir_tree="backend/\n├── src/\n└── tests/",
            ),
        ],
        "cross_deps": [],
        "coordination_complexity": 1,
        "branch_prefix": "af",
        "sensitive_paths": [".env"],
        "qa_requirements": {"backend": ["pytest", "ruff check ."]},
        "git_remote": "",
        "monorepo": False,
    }
    defaults.update(kwargs)
    return DiscoveryContext(**defaults)


def test_generates_orchestrator_yaml(tmp_path):
    """Generates orchestrator.yaml."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path)
    generate_artifacts(ctx, tmp_path, force=True)

    config = tmp_path / "orchestrator.yaml"
    assert config.exists()
    content = config.read_text()
    assert "test-project" in content
    assert "backend" in content


def test_generates_root_claude_md(tmp_path):
    """Generates root CLAUDE.md."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path)
    generate_artifacts(ctx, tmp_path, force=True)

    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "Orchestrator" in content
    assert "backend" in content


def test_generates_module_claude_md(tmp_path):
    """Generates per-module CLAUDE.md."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path)
    generate_artifacts(ctx, tmp_path, force=True)

    mod_claude = tmp_path / "backend" / "CLAUDE.md"
    assert mod_claude.exists()
    content = mod_claude.read_text()
    assert "FIRST ACTION" in content
    assert "Python" in content
    assert "pytest" in content


def test_generates_module_status_md(tmp_path):
    """Generates per-module STATUS.md."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path)
    generate_artifacts(ctx, tmp_path, force=True)

    status = tmp_path / "backend" / "STATUS.md"
    assert status.exists()
    content = status.read_text()
    assert "Active Work" in content


def test_generates_contracts_md_when_complex(tmp_path):
    """CONTRACTS.md only generated when coordination_complexity >= 2."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()

    dep = CrossModuleDep(
        from_module="frontend",
        to_module="backend",
        interface_type="api",
        description="REST API",
    )
    ctx = _make_ctx(
        tmp_path,
        coordination_complexity=2,
        cross_deps=[dep],
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python"]),
            ModuleProfile(name="frontend", path="frontend", tech_stack=["Node.js"]),
        ],
    )
    generate_artifacts(ctx, tmp_path, force=True)

    contracts = tmp_path / "CONTRACTS.md"
    assert contracts.exists()
    assert "API Contracts" in contracts.read_text()


def test_no_contracts_when_simple(tmp_path):
    """CONTRACTS.md not generated when coordination_complexity < 2."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path, coordination_complexity=1)
    generate_artifacts(ctx, tmp_path, force=True)

    contracts = tmp_path / "CONTRACTS.md"
    assert not contracts.exists()


def test_creates_orchestrator_dir(tmp_path):
    """Creates .orchestrator/logs and .orchestrator/sessions."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(tmp_path)
    generate_artifacts(ctx, tmp_path, force=True)

    assert (tmp_path / ".orchestrator" / "logs").is_dir()
    assert (tmp_path / ".orchestrator" / "sessions").is_dir()


def test_skip_existing_files_without_force(tmp_path):
    """Existing files are not overwritten without --force."""
    (tmp_path / "backend").mkdir()
    config = tmp_path / "orchestrator.yaml"
    config.write_text("original content")

    ctx = _make_ctx(tmp_path)
    written = generate_artifacts(ctx, tmp_path, force=False)

    # orchestrator.yaml should NOT be in written list (skipped)
    written_names = {p.name for p in written}
    assert "orchestrator.yaml" not in written_names
    assert config.read_text() == "original content"


def test_qa_gates_in_config(tmp_path):
    """QA requirements appear as custom gates in orchestrator.yaml."""
    (tmp_path / "backend").mkdir()
    ctx = _make_ctx(
        tmp_path,
        qa_requirements={"backend": ["pytest", "ruff check ."]},
    )
    generate_artifacts(ctx, tmp_path, force=True)

    content = (tmp_path / "orchestrator.yaml").read_text()
    assert "qa_gates:" in content
    assert "pytest" in content
    assert "ruff" in content
