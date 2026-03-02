"""Tests for the interactive interview engine."""

from lindy_orchestrator.discovery.interview import run_interview
from lindy_orchestrator.models import ModuleProfile, ProjectProfile


def _make_profile(modules=None, **kwargs):
    defaults = {
        "name": "test-project",
        "root": "/tmp/test",
        "modules": modules or [],
        "cross_module_files": [],
        "git_remote": "",
        "default_branch": "main",
        "detected_ci": "",
        "monorepo": len(modules or []) > 1,
    }
    defaults.update(kwargs)
    return ProjectProfile(**defaults)


def _make_module(name, tech=None, patterns=None, test_cmds=None, lint_cmds=None):
    return ModuleProfile(
        name=name,
        path=name,
        tech_stack=tech or ["Python"],
        detected_patterns=patterns or [],
        test_commands=test_cmds or ["pytest"],
        lint_commands=lint_cmds or ["ruff check ."],
    )


def test_non_interactive_single_module():
    """Non-interactive mode produces a valid context for single module."""
    mod = _make_module("backend", patterns=["REST API"])
    profile = _make_profile(modules=[mod])

    ctx = run_interview(profile, non_interactive=True)

    assert ctx.project_name == "test-project"
    assert len(ctx.modules) == 1
    assert ctx.modules[0].name == "backend"
    assert ctx.branch_prefix == "af"
    assert ctx.coordination_complexity == 1  # single module = loose
    assert len(ctx.sensitive_paths) > 0  # defaults applied


def test_non_interactive_multi_module():
    """Non-interactive mode for multi-module sets moderate complexity."""
    mods = [
        _make_module("backend", patterns=["REST API"]),
        _make_module("frontend", tech=["Node.js"], patterns=["frontend SPA"]),
    ]
    profile = _make_profile(modules=mods)

    ctx = run_interview(profile, non_interactive=True)

    assert len(ctx.modules) == 2
    assert ctx.coordination_complexity == 2  # moderate for multi-module non-interactive
    assert ctx.monorepo is True


def test_qa_requirements_from_detected_commands():
    """Auto-detected test/lint commands become QA requirements."""
    mod = _make_module(
        "backend",
        test_cmds=["pytest --tb=short"],
        lint_cmds=["mypy src/", "ruff check ."],
    )
    profile = _make_profile(modules=[mod])

    ctx = run_interview(profile, non_interactive=True)

    assert "backend" in ctx.qa_requirements
    reqs = ctx.qa_requirements["backend"]
    assert "pytest --tb=short" in reqs
    assert "mypy src/" in reqs
    assert "ruff check ." in reqs


def test_sensitive_paths_defaults():
    """Default sensitive paths are always included."""
    profile = _make_profile(modules=[_make_module("svc")])
    ctx = run_interview(profile, non_interactive=True)

    assert ".env" in ctx.sensitive_paths
    assert "*.key" in ctx.sensitive_paths
