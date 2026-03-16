"""Helpers for the unified onboard command — constants, detection, parsing, plan display.

Split from cli_onboard.py to keep each file under the structural 500-line limit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rich.console import Console

from .config import CONFIG_FILENAME
from .models import CrossModuleDep, DiscoveryContext, ModuleProfile

# ---------------------------------------------------------------------------
# Constants (preserved from cli_init.py)
# ---------------------------------------------------------------------------

_MODULE_MARKERS = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java/Kotlin",
    "CMakeLists.txt": "C/C++",
    "Makefile": "C/C++",
}

_IGNORED_DIRS = {
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".eggs",
    "target",
    ".next",
    ".nuxt",
    ".output",
    "vendor",
    "coverage",
    "htmlcov",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
}

# ---------------------------------------------------------------------------
# LLM scaffold prompt (preserved from cli_scaffold.py)
# ---------------------------------------------------------------------------

SCAFFOLD_SYSTEM_PROMPT = """\
You are a project architect. Analyze the following project description and \
produce a JSON structure that defines the project's orchestration scaffold.

You must return ONLY a valid JSON object (no markdown fencing, no explanation) \
with this exact structure:

{
  "project_name": "short-slug-name",
  "project_description": "One-line summary",
  "modules": [
    {
      "name": "module-name",
      "path": "module-name",
      "tech_stack": ["Python", "FastAPI"],
      "detected_patterns": ["REST API", "database ORM"],
      "test_commands": ["pytest"],
      "build_commands": ["pip install -e ."],
      "lint_commands": ["ruff check ."]
    }
  ],
  "cross_deps": [
    {
      "from_module": "frontend",
      "to_module": "backend",
      "interface_type": "api",
      "description": "REST API calls"
    }
  ],
  "coordination_complexity": 2,
  "branch_prefix": "af",
  "sensitive_paths": [".env", "*.key"],
  "qa_requirements": {
    "module-name": ["pytest", "ruff check ."]
  },
  "monorepo": true
}

Rules:
- "modules": At least one module. Each has name, path, tech_stack, and commands.
- "path": Relative directory path for the module (e.g., "backend", "frontend", ".").
  For a single-module project, use the module name as the path.
- "tech_stack": List of technologies (languages, frameworks).
- "detected_patterns": Architectural patterns (e.g., "REST API", "frontend SPA", \
"database ORM", "async job queue", "containerized", "microservices").
- "cross_deps": Dependencies between modules. Only include if there are 2+ modules.
  "interface_type" is one of: "api", "file", "database", "env_var", "message_queue".
- "coordination_complexity": 1 (single module or loosely coupled), \
2 (moderate cross-module deps), 3 (tight integration, shared state).
- "qa_requirements": Map of module name to list of QA commands.
- "sensitive_paths": Glob patterns for files that should never be committed.
- "monorepo": true if multiple modules in one repo, false for single module.
- Infer reasonable defaults for commands based on the tech stack.
"""


# ---------------------------------------------------------------------------
# Project state detection
# ---------------------------------------------------------------------------


def _has_config(cwd: Path) -> bool:
    """Check if config exists (new .orchestrator/config.yaml or legacy orchestrator.yaml)."""
    return (cwd / ".orchestrator" / "config.yaml").exists() or (cwd / CONFIG_FILENAME).exists()


def _has_source_files(cwd: Path) -> bool:
    """Check if the directory has any meaningful source files/modules."""
    for item in cwd.iterdir():
        if item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        if item.is_dir():
            tech = _detect_tech(item, 1)
            if tech:
                return True
        elif item.is_file():
            for marker in _MODULE_MARKERS:
                if item.name == marker:
                    return True
    return False


# ---------------------------------------------------------------------------
# Helpers preserved from cli_init.py
# ---------------------------------------------------------------------------


def _detect_modules(root: Path, max_depth: int) -> list[tuple[str, str]]:
    """Auto-detect project modules by scanning for marker files."""
    modules = []
    for item in sorted(root.iterdir()):
        if not item.is_dir() or item.name.startswith(".") or item.name in _IGNORED_DIRS:
            continue
        tech = _detect_tech(item, max_depth)
        if tech:
            modules.append((item.name, tech))
    return modules


def _detect_tech(path: Path, depth: int) -> str:
    """Detect technology in a directory."""
    for marker, tech in _MODULE_MARKERS.items():
        if (path / marker).exists():
            return tech
    if (path / "src").is_dir():
        return "source directory"
    if depth > 1:
        for sub in path.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                result = _detect_tech(sub, depth - 1)
                if result:
                    return result
    return ""


def _generate_config(project_name: str, modules: list[tuple[str, str]]) -> str:
    """Generate orchestrator.yaml content."""
    lines = [
        f"# {CONFIG_FILENAME} — lindy-orchestrator configuration",
        "",
        "project:",
        f'  name: "{project_name}"',
        '  branch_prefix: "af"',
        "",
        "modules:",
    ]
    for name, tech in modules:
        lines.append(f"  - name: {name}")
        lines.append(f"    path: {name}/")
        lines.append(f"    # tech: {tech}")
        lines.append(f"    # repo: yourorg/{project_name}-{name}")
        lines.append("    # ci_workflow: ci.yml")
        lines.append("")

    lines.extend(
        [
            "planner:",
            "  mode: cli  # cli | api",
            "  # model: claude-sonnet-4-20250514  # for api mode",
            "",
            "dispatcher:",
            "  timeout_seconds: 1800",
            "  permission_mode: bypassPermissions",
            "",
            "# qa_gates:",
            "#   custom:",
            "#     - name: pytest",
            '#       command: "pytest -x -q --tb=short"',
            '#       cwd: "{module_path}"',
            "#     - name: lint",
            '#       command: "ruff check ."',
            '#       cwd: "{module_path}"',
            "#       diff_only: true  # only lint changed files",
            "",
            "safety:",
            "  dry_run: false",
            "  max_retries_per_task: 2",
            "  max_parallel: 3",
            "",
            "mailbox:",
            "  enabled: true",
        ]
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Helpers preserved from cli_scaffold.py
# ---------------------------------------------------------------------------


def _build_scaffold_prompt(description: str) -> str:
    """Build the full prompt for the scaffold LLM call."""
    return f"""{SCAFFOLD_SYSTEM_PROMPT}

## Project Description

{description}

Return ONLY the JSON object."""


def parse_scaffold_response(output: str) -> dict:
    """Parse the LLM JSON response into a dict.

    Handles markdown code fences and extracts the JSON object.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", output)
    cleaned = re.sub(r"```\s*$", "", cleaned)

    json_match = re.search(r"\{[\s\S]*\}", cleaned)
    if json_match:
        return json.loads(json_match.group())
    return json.loads(cleaned)


def scaffold_response_to_context(data: dict, output_dir: str = ".") -> DiscoveryContext:
    """Convert parsed LLM scaffold response into a DiscoveryContext."""
    modules = []
    for m in data.get("modules", []):
        modules.append(
            ModuleProfile(
                name=m["name"],
                path=m.get("path", m["name"]),
                tech_stack=m.get("tech_stack", []),
                detected_patterns=m.get("detected_patterns", []),
                test_commands=m.get("test_commands", []),
                build_commands=m.get("build_commands", []),
                lint_commands=m.get("lint_commands", []),
            )
        )

    cross_deps = []
    for d in data.get("cross_deps", []):
        cross_deps.append(
            CrossModuleDep(
                from_module=d["from_module"],
                to_module=d["to_module"],
                interface_type=d.get("interface_type", ""),
                description=d.get("description", ""),
            )
        )

    return DiscoveryContext(
        project_name=data.get("project_name", "project"),
        project_description=data.get("project_description", ""),
        root=output_dir,
        modules=modules,
        cross_deps=cross_deps,
        coordination_complexity=data.get("coordination_complexity", 1),
        branch_prefix=data.get("branch_prefix", "af"),
        sensitive_paths=data.get("sensitive_paths", [".env"]),
        qa_requirements=data.get("qa_requirements", {}),
        monorepo=data.get("monorepo", False),
    )


# ---------------------------------------------------------------------------
# Plan display
# ---------------------------------------------------------------------------


def _show_plan(
    console: Console, mode: str, cwd: Path, files_to_create: list[str], modules_info: list[str]
) -> None:
    """Display the interactive plan before executing."""
    mode_labels = {
        "scaffold": "Scaffold (LLM-driven, empty project)",
        "init_onboard": "Init + Onboard (detect modules, interview, generate artifacts)",
        "re_onboard": "Re-onboard (update existing configuration)",
    }
    console.print("\n[bold cyan]Onboarding Plan[/]")
    console.print(f"  Mode: [bold]{mode_labels.get(mode, mode)}[/]")
    console.print(f"  Directory: [bold]{cwd}[/]")

    if modules_info:
        console.print(f"\n  Modules detected ({len(modules_info)}):")
        for info in modules_info:
            console.print(f"    - {info}")

    if files_to_create:
        console.print(f"\n  Files to create/modify ({len(files_to_create)}):")
        for f in files_to_create:
            console.print(f"    - {f}")

    console.print()
