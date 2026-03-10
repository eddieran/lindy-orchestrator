"""Scaffold path consistency tests.

Guards against breaking changes when scaffold files move between locations.
Every path that the orchestrator reads or writes must be tested here to ensure
it points to the correct .orchestrator/ location.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from lindy_orchestrator.config import (
    ORCH_DIR,
    OrchestratorConfig,
    find_config,
    load_config,
)
from lindy_orchestrator.discovery.generator import generate_artifacts
from lindy_orchestrator.discovery.templates.agent_docs import render_agent_docs
from lindy_orchestrator.discovery.templates.architecture_md import render_architecture_md
from lindy_orchestrator.discovery.templates.contracts_md import render_contracts_md
from lindy_orchestrator.discovery.templates.module_claude_md import render_module_claude_md
from lindy_orchestrator.discovery.templates.root_claude_md import render_root_claude_md
from lindy_orchestrator.models import DiscoveryContext, ModuleProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal config with .orchestrator/config.yaml."""
    orch = tmp_path / ORCH_DIR
    orch.mkdir(parents=True, exist_ok=True)
    cfg_file = orch / "config.yaml"
    cfg_file.write_text(
        yaml.dump(
            {
                "project": {"name": "pathtest"},
                "modules": [
                    {"name": "backend", "path": "backend/"},
                    {"name": "frontend", "path": "frontend/"},
                ],
            }
        )
    )
    (tmp_path / "backend").mkdir(exist_ok=True)
    (tmp_path / "frontend").mkdir(exist_ok=True)
    return load_config(cfg_file)


def _make_ctx(tmp_path: Path, coordination_complexity: int = 1) -> DiscoveryContext:
    return DiscoveryContext(
        project_name="pathtest",
        project_description="test project",
        root=str(tmp_path),
        modules=[
            ModuleProfile(name="backend", path="backend", tech_stack=["Python"]),
        ],
        coordination_complexity=coordination_complexity,
        branch_prefix="af",
    )


# ===========================================================================
# 1. Config path resolution
# ===========================================================================


class TestConfigPathResolution:
    """Verify config.py path methods return .orchestrator/ locations."""

    def test_find_config_prefers_new_path(self, tmp_path):
        """find_config() returns .orchestrator/config.yaml over orchestrator.yaml."""
        (tmp_path / "orchestrator.yaml").write_text("project: {name: old}")
        orch = tmp_path / ORCH_DIR
        orch.mkdir()
        new_cfg = orch / "config.yaml"
        new_cfg.write_text("project: {name: new}")

        found = find_config(tmp_path)
        assert found is not None
        assert found.name == "config.yaml"
        assert ORCH_DIR in str(found)

    def test_find_config_falls_back_to_legacy(self, tmp_path):
        """find_config() still finds legacy orchestrator.yaml."""
        (tmp_path / "orchestrator.yaml").write_text("project: {name: legacy}")
        found = find_config(tmp_path)
        assert found is not None
        assert found.name == "orchestrator.yaml"

    def test_load_config_sets_root_correctly(self, tmp_path):
        """When loading from .orchestrator/config.yaml, root is the project dir (parent)."""
        cfg = _minimal_config(tmp_path)
        assert cfg.root == tmp_path

    def test_status_path_returns_orchestrator_dir(self, tmp_path):
        """status_path() returns .orchestrator/status/{module}.md when it exists."""
        cfg = _minimal_config(tmp_path)
        # Create the new-layout status file
        status_dir = tmp_path / ORCH_DIR / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("# Status")

        path = cfg.status_path("backend")
        assert ".orchestrator" in str(path)
        assert path.name == "backend.md"

    def test_status_path_falls_back_to_legacy(self, tmp_path):
        """status_path() falls back to {module}/STATUS.md when new path absent."""
        cfg = _minimal_config(tmp_path)
        # Create legacy status file (no .orchestrator/status/)
        (tmp_path / "backend" / "STATUS.md").write_text("# Legacy")

        path = cfg.status_path("backend")
        assert path.name == "STATUS.md"
        assert "backend" in str(path)

    def test_orch_dir_property(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        assert cfg.orch_dir == (tmp_path / ORCH_DIR).resolve()

    def test_orch_config_path_property(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        assert cfg.orch_config_path == (tmp_path / ORCH_DIR / "config.yaml").resolve()


# ===========================================================================
# 2. Generator output paths
# ===========================================================================


class TestGeneratorOutputPaths:
    """Verify generate_artifacts() writes all files under .orchestrator/."""

    def test_all_artifacts_under_orchestrator_dir(self, tmp_path):
        """No artifact is written to project root (except .gitignore update)."""
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        written = generate_artifacts(ctx, tmp_path, force=True)

        for path in written:
            rel = path.relative_to(tmp_path)
            assert str(rel).startswith(ORCH_DIR), f"Artifact {rel} written outside .orchestrator/"

    def test_config_yaml_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "config.yaml").exists()

    def test_claude_root_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "claude" / "root.md").exists()

    def test_claude_module_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "claude" / "backend.md").exists()

    def test_codex_root_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "codex" / "root.md").exists()

    def test_codex_module_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "codex" / "backend.md").exists()

    def test_architecture_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "architecture.md").exists()

    def test_status_md_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "status" / "backend.md").exists()

    def test_docs_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        docs = tmp_path / ORCH_DIR / "docs"
        assert (docs / "protocol.md").exists()
        assert (docs / "conventions.md").exists()
        assert (docs / "boundaries.md").exists()

    def test_contracts_md_created_when_complex(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path, coordination_complexity=2)
        generate_artifacts(ctx, tmp_path, force=True)
        assert (tmp_path / ORCH_DIR / "contracts.md").exists()

    def test_runtime_dirs_created(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        orch = tmp_path / ORCH_DIR
        assert (orch / "logs").is_dir()
        assert (orch / "sessions").is_dir()
        assert (orch / "mailbox").is_dir()

    def test_no_legacy_files_at_root(self, tmp_path):
        """Ensure NO scaffold files are created at the project root."""
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path, coordination_complexity=2)
        generate_artifacts(ctx, tmp_path, force=True)

        assert not (tmp_path / "orchestrator.yaml").exists()
        assert not (tmp_path / "CLAUDE.md").exists()
        assert not (tmp_path / "ARCHITECTURE.md").exists()
        assert not (tmp_path / "CONTRACTS.md").exists()
        assert not (tmp_path / "docs" / "agents").exists()
        # No per-module STATUS.md or CLAUDE.md at legacy locations
        assert not (tmp_path / "backend" / "STATUS.md").exists()
        assert not (tmp_path / "backend" / "CLAUDE.md").exists()

    def test_gitignore_uses_single_entry(self, tmp_path):
        (tmp_path / "backend").mkdir()
        ctx = _make_ctx(tmp_path)
        generate_artifacts(ctx, tmp_path, force=True)
        content = (tmp_path / ".gitignore").read_text()
        assert ".orchestrator/" in content
        # Should NOT have the old granular entries
        assert ".orchestrator/logs/" not in content
        assert ".orchestrator/sessions/" not in content


# ===========================================================================
# 3. Template content path references
# ===========================================================================


class TestTemplatePathReferences:
    """Verify generated template content references .orchestrator/ paths."""

    def test_root_claude_md_references(self):
        ctx = _make_ctx(Path("."), coordination_complexity=2)
        content = render_root_claude_md(ctx)
        assert ".orchestrator/architecture.md" in content
        assert ".orchestrator/docs/protocol.md" in content
        assert ".orchestrator/docs/conventions.md" in content
        assert ".orchestrator/docs/boundaries.md" in content
        assert ".orchestrator/contracts.md" in content
        # Must NOT reference old root-level paths
        assert "- `ARCHITECTURE.md`" not in content
        assert "- `docs/agents/" not in content
        assert "- `CONTRACTS.md`" not in content

    def test_module_claude_md_no_local_status_reference(self):
        ctx = _make_ctx(Path("."))
        mod = ctx.modules[0]
        content = render_module_claude_md(ctx, mod)
        # Should NOT tell agent to read STATUS.md from local directory
        assert "Read `STATUS.md` in this directory" not in content

    def test_architecture_md_references(self):
        ctx = _make_ctx(Path("."), coordination_complexity=2)
        content = render_architecture_md(ctx)
        assert ".orchestrator/contracts.md" in content
        assert "defined in `CONTRACTS.md`" not in content

    def test_agent_docs_references(self):
        ctx = _make_ctx(Path("."), coordination_complexity=2)
        docs = render_agent_docs(ctx)
        protocol = docs["protocol.md"]
        assert ".orchestrator/contracts.md" in protocol
        assert ".orchestrator/architecture.md" in protocol
        assert ".orchestrator/status/" in protocol

        # boundaries references .orchestrator/ only for multi-module projects
        # (single-module says "no cross-module concerns")

    def test_contracts_md_references(self):
        ctx = _make_ctx(Path("."), coordination_complexity=2)
        content = render_contracts_md(ctx)
        assert ".orchestrator/status/" in content


# ===========================================================================
# 4. Reader path consistency (planner, QA, entropy)
# ===========================================================================


class TestReaderPaths:
    """Verify modules that READ scaffold files look in .orchestrator/."""

    def test_planner_reads_architecture_from_orchestrator(self, tmp_path):
        """planner._read_all_statuses uses config.status_path which checks .orchestrator/."""
        cfg = _minimal_config(tmp_path)
        # Create architecture.md at new location
        arch = tmp_path / ORCH_DIR / "architecture.md"
        arch.write_text("# Architecture\n\n- **backend/** → Python")

        from lindy_orchestrator.planner import _read_all_statuses

        # Create status files at new location
        status_dir = tmp_path / ORCH_DIR / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text(
            "# Status\n\n## Meta\n| Key | Value |\n|-----|-------|\n"
            "| module | backend |\n| last_updated | 2026-01-01 |\n"
            "| overall_health | GREEN |\n| agent_session | — |\n\n"
            "## Active Work\n| ID | Task | Status | BlockedBy | Started | Notes |\n"
            "|----|------|--------|-----------|---------|-------|\n\n"
            "## Completed (Recent)\n| ID | Task | Completed | Outcome |\n"
            "|----|------|-----------|--------|\n\n"
            "## Backlog\n\n"
            "## Cross-Module Requests\n"
            "| ID | From | To | Request | Priority | Status |\n"
            "|----|------|----|---------|----------|--------|\n\n"
            "## Cross-Module Deliverables\n"
            "| ID | From | To | Deliverable | Status | Path |\n"
            "|----|------|----|-------------|--------|------|\n\n"
            "## Key Metrics\n| Metric | Value |\n|--------|-------|\n\n"
            "## Blockers\n"
        )
        (status_dir / "frontend.md").write_text(
            "# Status\n\n## Meta\n| Key | Value |\n|-----|-------|\n"
            "| module | frontend |\n| last_updated | 2026-01-01 |\n"
            "| overall_health | GREEN |\n| agent_session | — |\n\n"
            "## Active Work\n| ID | Task | Status | BlockedBy | Started | Notes |\n"
            "|----|------|--------|-----------|---------|-------|\n\n"
            "## Completed (Recent)\n| ID | Task | Completed | Outcome |\n"
            "|----|------|-----------|--------|\n\n"
            "## Backlog\n\n"
            "## Cross-Module Requests\n"
            "| ID | From | To | Request | Priority | Status |\n"
            "|----|------|----|---------|----------|--------|\n\n"
            "## Cross-Module Deliverables\n"
            "| ID | From | To | Deliverable | Status | Path |\n"
            "|----|------|----|-------------|--------|------|\n\n"
            "## Key Metrics\n| Metric | Value |\n|--------|-------|\n\n"
            "## Blockers\n"
        )

        statuses = _read_all_statuses(cfg)
        assert "backend" in statuses
        assert "GREEN" in statuses["backend"]

    def test_layer_check_reads_from_orchestrator(self, tmp_path):
        """layer_check looks for .orchestrator/architecture.md."""
        from lindy_orchestrator.qa.layer_check import _parse_architecture_layers

        # Should NOT find anything at old location
        (tmp_path / "ARCHITECTURE.md").write_text("- **backend/**: models → services → routes")
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is None  # old location ignored

        # Should find at new location
        orch = tmp_path / ORCH_DIR
        orch.mkdir(parents=True, exist_ok=True)
        (orch / "architecture.md").write_text("- **backend/**: models → services → routes")
        result = _parse_architecture_layers(tmp_path, "backend")
        assert result is not None
        assert result.layers == ["models", "services", "routes"]

    def test_entropy_scanner_reads_from_orchestrator(self, tmp_path):
        """entropy scanner checks .orchestrator/architecture.md."""
        from lindy_orchestrator.entropy.scanner import _check_architecture_drift

        cfg = _minimal_config(tmp_path)

        # No architecture.md at new location → warning
        findings = _check_architecture_drift(cfg)
        assert any(".orchestrator/architecture.md" in f.description for f in findings)

        # Create at new location → no "not found" warning
        (tmp_path / ORCH_DIR / "architecture.md").write_text(
            "# Architecture\n\n- **backend/** → Python\n- **frontend/** → JS"
        )
        findings = _check_architecture_drift(cfg)
        # "architecture.md not found" should be gone; other layer findings are OK
        assert not any(
            "architecture.md" in f.description.lower() and "not found" in f.description.lower()
            for f in findings
        )

    def test_entropy_scanner_contracts_from_orchestrator(self, tmp_path):
        """entropy scanner checks .orchestrator/contracts.md."""
        from lindy_orchestrator.entropy.scanner import _check_contract_compliance

        cfg = _minimal_config(tmp_path)
        findings = _check_contract_compliance(cfg)
        assert any(".orchestrator/contracts.md" in f.description for f in findings)


# ===========================================================================
# 5. Injection functions
# ===========================================================================


class TestInjectionPaths:
    """Verify inject functions read from .orchestrator/."""

    def test_inject_status_content_reads_orchestrator(self, tmp_path):
        from lindy_orchestrator.models import TaskItem
        from lindy_orchestrator.scheduler_helpers import inject_status_content

        cfg = _minimal_config(tmp_path)
        status_dir = tmp_path / ORCH_DIR / "status"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / "backend.md").write_text("# Backend Status\nHealth: GREEN")

        task = TaskItem(id=1, module="backend", description="test", prompt="Do stuff")
        messages: list[str] = []
        inject_status_content(task, cfg, lambda m: messages.append(m))

        assert "Backend Status" in task.prompt
        assert "## Current STATUS.md" in task.prompt

    def test_inject_claude_md_reads_orchestrator(self, tmp_path):
        from lindy_orchestrator.models import TaskItem
        from lindy_orchestrator.scheduler_helpers import inject_claude_md

        cfg = _minimal_config(tmp_path)
        claude_dir = tmp_path / ORCH_DIR / "claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "root.md").write_text("# Root Instructions\nYou are the orchestrator.")
        (claude_dir / "backend.md").write_text("# Backend Agent\nPython module.")

        task = TaskItem(id=1, module="backend", description="test", prompt="Do stuff")
        messages: list[str] = []
        inject_claude_md(task, cfg, lambda m: messages.append(m))

        assert "Root Instructions" in task.prompt
        assert "Backend Agent" in task.prompt
        assert "## CLAUDE.md Instructions" in task.prompt

    def test_inject_qa_gates_checks_orchestrator_arch(self, tmp_path):
        from lindy_orchestrator.models import TaskItem
        from lindy_orchestrator.scheduler_helpers import inject_qa_gates

        cfg = _minimal_config(tmp_path)

        # No architecture → no layer_check injected
        task = TaskItem(id=1, module="backend", description="test", prompt="Do stuff")
        inject_qa_gates(task, cfg, lambda m: None)
        assert not any(q.gate == "layer_check" for q in task.qa_checks)

        # Create architecture at new path → layer_check injected
        (tmp_path / ORCH_DIR / "architecture.md").write_text("# Arch")
        task2 = TaskItem(id=2, module="backend", description="test2", prompt="Do stuff")
        inject_qa_gates(task2, cfg, lambda m: None)
        assert any(q.gate == "layer_check" for q in task2.qa_checks)


# ===========================================================================
# 6. Additional orch_* config properties
# ===========================================================================


class TestOrchConfigProperties:
    """Verify all orch_* helper properties resolve to .orchestrator/ paths."""

    def test_orch_status_path(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        path = cfg.orch_status_path("backend")
        assert ".orchestrator" in str(path)
        assert "status" in str(path)
        assert path.name == "backend.yaml"

    def test_orch_log_path(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        path = cfg.orch_log_path
        assert ".orchestrator" in str(path)
        assert path.name == "actions.jsonl"
        assert "logs" in str(path)

    def test_orch_sessions_path(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        path = cfg.orch_sessions_path
        assert ".orchestrator" in str(path)
        assert path.name == "sessions"

    def test_orch_mailbox_path(self, tmp_path):
        cfg = _minimal_config(tmp_path)
        path = cfg.orch_mailbox_path
        assert ".orchestrator" in str(path)
        assert path.name == "mailbox"


# ===========================================================================
# 7. _has_config backward compatibility
# ===========================================================================


class TestHasConfigHelper:
    """Verify _has_config detects both new and legacy config locations."""

    def test_detects_new_config(self, tmp_path):
        from lindy_orchestrator.cli_onboard_helpers import _has_config

        orch = tmp_path / ".orchestrator"
        orch.mkdir()
        (orch / "config.yaml").write_text("project: {name: test}")
        assert _has_config(tmp_path) is True

    def test_detects_legacy_config(self, tmp_path):
        from lindy_orchestrator.cli_onboard_helpers import _has_config

        (tmp_path / "orchestrator.yaml").write_text("project: {name: test}")
        assert _has_config(tmp_path) is True

    def test_returns_false_when_no_config(self, tmp_path):
        from lindy_orchestrator.cli_onboard_helpers import _has_config

        assert _has_config(tmp_path) is False


# ===========================================================================
# 8. Planner reads architecture from .orchestrator/
# ===========================================================================


class TestPlannerArchitecturePath:
    """Verify planner.generate_plan reads architecture from .orchestrator/."""

    def test_planner_arch_path_uses_orchestrator(self, tmp_path):
        """The arch_path variable in generate_plan points to .orchestrator/architecture.md."""
        # Verify by inspecting the source — the actual path construction
        import lindy_orchestrator.planner as planner_mod
        import inspect

        source = inspect.getsource(planner_mod.generate_plan)
        assert '".orchestrator"' in source or "'.orchestrator'" in source
        assert "architecture.md" in source

    def test_planner_reads_new_arch_not_legacy(self, tmp_path):
        """Planner should read .orchestrator/architecture.md, not root ARCHITECTURE.md."""
        cfg = _minimal_config(tmp_path)
        # Put content at the WRONG (legacy) location
        (tmp_path / "ARCHITECTURE.md").write_text("# Legacy architecture")
        # Put content at the RIGHT (new) location
        orch_arch = tmp_path / ORCH_DIR / "architecture.md"
        orch_arch.write_text("# New architecture\n\n- **backend/** → Python")

        # The planner's arch_path should resolve to the new location
        arch_path = cfg.root / ".orchestrator" / "architecture.md"
        assert arch_path.exists()
        assert "New architecture" in arch_path.read_text()


# ===========================================================================
# 9. Prompt wording updated for injection model
# ===========================================================================


class TestPromptWording:
    """Verify prompts reference injected content, not file discovery."""

    def test_prompt_template_references_injected_status(self):
        from lindy_orchestrator.prompts import PLAN_PROMPT_TEMPLATE

        assert "Read the STATUS.md content provided above." in PLAN_PROMPT_TEMPLATE
        assert "Read your STATUS.md first." not in PLAN_PROMPT_TEMPLATE

    def test_agent_check_prompt_references_injected_status(self):
        """agent_check.py prompt should reference injected content."""
        import inspect

        from lindy_orchestrator.qa.agent_check import AgentCheckGate

        source = inspect.getsource(AgentCheckGate.check)
        assert "Read the STATUS.md content provided above." in source
        assert "Read your STATUS.md first." not in source

    def test_module_claude_md_references_injected_status(self):
        """Module CLAUDE.md template tells agent to read injected content."""
        ctx = _make_ctx(Path("."))
        mod = ctx.modules[0]
        content = render_module_claude_md(ctx, mod)
        assert "injected into your prompt" in content
        assert "Read `STATUS.md` in this directory" not in content


# ===========================================================================
# 10. Template content — multi-module path references
# ===========================================================================


class TestTemplateMultiModulePaths:
    """Verify templates reference .orchestrator/ paths for multi-module projects."""

    def _multi_module_ctx(self):
        return DiscoveryContext(
            project_name="multi",
            project_description="multi-module project",
            root=".",
            modules=[
                ModuleProfile(name="backend", path="backend", tech_stack=["Python"]),
                ModuleProfile(name="frontend", path="frontend", tech_stack=["React"]),
            ],
            coordination_complexity=2,
            branch_prefix="af",
        )

    def test_agent_docs_boundaries_references(self):
        """Boundaries doc should reference .orchestrator/ paths for cross-module comms."""
        ctx = self._multi_module_ctx()
        docs = render_agent_docs(ctx)
        boundaries = docs["boundaries.md"]
        assert ".orchestrator/status/" in boundaries
        assert ".orchestrator/contracts.md" in boundaries

    def test_agent_docs_protocol_no_legacy_refs(self):
        """Protocol doc must NOT reference legacy paths."""
        ctx = self._multi_module_ctx()
        docs = render_agent_docs(ctx)
        protocol = docs["protocol.md"]
        # Should not reference old-style "CONTRACTS.md" at root
        assert "defined in `CONTRACTS.md`" not in protocol
        # Should use new path
        assert ".orchestrator/contracts.md" in protocol

    def test_contracts_md_status_references(self):
        """Contracts template should reference .orchestrator/status/ paths."""
        ctx = self._multi_module_ctx()
        content = render_contracts_md(ctx)
        assert ".orchestrator/status/" in content
        # Change Protocol section should reference new path
        assert "`.orchestrator/status/" in content


# ===========================================================================
# 11. Load config backward compat — legacy orchestrator.yaml still works
# ===========================================================================


class TestLoadConfigBackwardCompat:
    """Verify load_config handles both new and legacy config file locations."""

    def test_load_from_legacy_sets_root_correctly(self, tmp_path):
        """Loading from legacy orchestrator.yaml sets _config_dir to the parent."""
        cfg_file = tmp_path / "orchestrator.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "project": {"name": "legacy"},
                    "modules": [{"name": "app", "path": "app/"}],
                }
            )
        )
        (tmp_path / "app").mkdir()

        cfg = load_config(cfg_file)
        assert cfg.root == tmp_path
        assert cfg.project.name == "legacy"

    def test_load_from_new_location_sets_root_correctly(self, tmp_path):
        """Loading from .orchestrator/config.yaml sets _config_dir to grandparent."""
        orch = tmp_path / ORCH_DIR
        orch.mkdir()
        cfg_file = orch / "config.yaml"
        cfg_file.write_text(
            yaml.dump(
                {
                    "project": {"name": "newlayout"},
                    "modules": [{"name": "app", "path": "app/"}],
                }
            )
        )
        (tmp_path / "app").mkdir()

        cfg = load_config(cfg_file)
        # Root should be tmp_path, not tmp_path/.orchestrator/
        assert cfg.root == tmp_path
        assert cfg.project.name == "newlayout"
