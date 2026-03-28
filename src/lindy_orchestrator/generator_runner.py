"""Role-aware generator dispatch and prompt construction."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from .config import OrchestratorConfig
from .models import DispatchResult, TaskSpec
from .providers import create_provider

log = logging.getLogger(__name__)


class GeneratorRunner:
    """Owns generator-visible prompt assembly and dispatch execution."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.provider = create_provider(config.dispatcher)

    def generator_prompt(self, task: TaskSpec) -> str:
        return task.generator_prompt or task.prompt

    def resolve_working_dir(
        self,
        task: TaskSpec,
        worktree_path: Path | None,
    ) -> tuple[Path, Path]:
        """Resolve working_dir and module_dir for generator dispatch."""
        if worktree_path:
            working_dir = worktree_path
            if task.module in ("root", "*"):
                module_dir = worktree_path
            else:
                mod = self.config.get_module(task.module)
                module_dir = (worktree_path / mod.path).resolve()
            return working_dir, module_dir

        return self.config.root.resolve(), self.config.module_path(task.module)

    def build_prompt(
        self,
        task: TaskSpec,
        branch_name: str,
        worktree_path: Path | None,
        dispatches: int,
        progress: Callable[[str], None],
    ) -> str:
        """Build the generator-visible prompt for the current dispatch."""
        if dispatches != 0:
            return task.prompt

        status_content = self._status_section(task, progress)
        instructions = self._instructions_section(task, progress)
        branch_instructions = self._branch_section(branch_name, worktree_path)
        parts = [
            part
            for part in [
                status_content,
                instructions,
                self.generator_prompt(task),
                branch_instructions,
            ]
            if part
        ]
        return "\n\n".join(parts)

    def dispatch(
        self,
        task: TaskSpec,
        branch_name: str,
        worktree_path: Path | None,
        dispatches: int,
        progress: Callable[[str], None],
        on_event: Callable[[dict], None],
    ) -> tuple[DispatchResult, Path]:
        """Dispatch the generator task and return the module directory."""
        task.prompt = self.build_prompt(task, branch_name, worktree_path, dispatches, progress)
        working_dir, module_dir = self.resolve_working_dir(task, worktree_path)
        result = self.provider.dispatch(
            module=task.module,
            working_dir=working_dir,
            prompt=task.prompt,
            on_event=on_event,
            stall_seconds=task.stall_seconds,
        )
        return result, module_dir

    def _status_section(self, task: TaskSpec, progress: Callable[[str], None]) -> str:
        path = self.config.status_path(task.module)
        if not path.exists():
            return ""
        try:
            progress(f"    [dim]Injected STATUS.md for {task.module}[/]")
            return f"## Current STATUS.md\n\n{path.read_text()}"
        except Exception:
            log.warning("Failed to read %s", path, exc_info=True)
            return ""

    def _instructions_section(self, task: TaskSpec, progress: Callable[[str], None]) -> str:
        provider = self.config.generator.resolved_provider(self.config.dispatcher.provider)
        provider_dir = "codex" if provider == "codex_cli" else "claude"
        header = "CODEX.md" if provider_dir == "codex" else "CLAUDE.md"
        orch_base = self.config.root / ".orchestrator"
        primary = orch_base / provider_dir
        fallback = orch_base / "claude" if provider_dir != "claude" else None

        sections: list[str] = []
        for name in ("root.md", f"{task.module}.md"):
            path = primary / name
            if not path.exists() and fallback:
                path = fallback / name
            if path.exists():
                try:
                    sections.append(path.read_text())
                except Exception:
                    log.warning("Failed to read %s", path, exc_info=True)
        if not sections:
            return ""

        progress("    [dim]Injected agent instructions[/]")
        return f"## {header} Instructions\n\n" + "\n\n".join(sections)

    def _branch_section(self, branch_name: str, worktree_path: Path | None) -> str:
        if worktree_path:
            return (
                "## IMPORTANT: Branch delivery requirements\n\n"
                f"You are already on branch `{branch_name}` (worktree isolation).\n"
                "Do NOT switch branches or run `git checkout`.\n"
                "When done:\n"
                "1. `git add` and `git commit` your changes\n"
                f"2. `git push -u origin {branch_name}` (push to remote)\n"
                "Do NOT skip the push step."
            )
        return (
            "## IMPORTANT: Branch delivery requirements\n\n"
            f"You MUST deliver your work on branch `{branch_name}`.\n"
            "Before starting work:\n"
            f"1. `git checkout -b {branch_name}` (create the branch)\n"
            "When done:\n"
            "2. `git add` and `git commit` your changes\n"
            f"3. `git push -u origin {branch_name}` (push to remote)\n"
            "Do NOT skip the push step."
        )
