"""Structural lint gate — file size, import boundaries, sensitive files, naming.

Each violation produces a remediation message that teaches the agent
HOW to fix the issue, not just what's wrong.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

from ..config import StructuralCheckConfig
from ..models import QAResult
from . import Violation, _format_violations, register


def run_structural_check(
    project_root: Path,
    module_name: str,
    config: StructuralCheckConfig | None = None,
    module_path: str | None = None,
) -> tuple[bool, list[Violation]]:
    """Run all structural checks on staged files in a module.

    Args:
        module_path: Resolved filesystem path for the module. When provided,
            used to compute the file prefix instead of ``module_name``.

    Returns (passed, violations).
    """
    if config is None:
        config = StructuralCheckConfig()

    file_prefix = _module_file_prefix(project_root, module_name, module_path)

    # Get staged files (or all tracked files if not in a git context)
    staged_files = _get_staged_files(project_root, file_prefix)

    violations: list[Violation] = []

    for filepath in staged_files:
        full_path = project_root / filepath

        if not full_path.is_file():
            continue

        # Check 1: File size (diff-aware: skips pre-existing violations)
        violations.extend(
            _check_file_size(full_path, filepath, config.max_file_lines, project_root)
        )

        # Check 2: Sensitive file patterns
        violations.extend(_check_sensitive_files(full_path, filepath, config.sensitive_patterns))

    # Check 3: Import boundary violations (module-level)
    # Skip for root modules — there's no cross-module boundary to enforce
    if config.enforce_module_boundary and file_prefix and module_name not in ("root", "*"):
        violations.extend(_check_import_boundary(project_root, file_prefix, staged_files))

    passed = len(violations) == 0
    return passed, violations


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_file_size(
    full_path: Path,
    rel_path: str,
    max_lines: int,
    project_root: Path | None = None,
) -> list[Violation]:
    """Flag files exceeding the line limit.

    Diff-aware: if the file was already over the limit at merge-base,
    the violation is marked with retryable=False (pre-existing).
    """
    try:
        line_count = sum(1 for _ in full_path.open("r", encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError):
        return []

    if line_count > max_lines:
        # Check if this is a pre-existing violation
        pre_existing = _was_over_limit_at_base(project_root, rel_path, max_lines)
        if pre_existing:
            return []  # skip entirely — not caused by the agent

        stem = full_path.stem
        suffix = full_path.suffix
        return [
            Violation(
                rule="file_size",
                file=rel_path,
                message=f"{rel_path} ({line_count} lines) exceeds {max_lines}-line limit.",
                remediation=(
                    f"Split into {stem}_core{suffix} (primary logic) and "
                    f"{stem}_helpers{suffix} (utilities/helpers). "
                    f"Keep each file under {max_lines} lines."
                ),
            )
        ]
    return []


def _was_over_limit_at_base(project_root: Path | None, rel_path: str, max_lines: int) -> bool:
    """Check if a file was already over the line limit at the merge-base commit."""
    if project_root is None:
        return False
    for base in ("main", "master"):
        try:
            merge_result = subprocess.run(
                ["git", "merge-base", base, "HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if merge_result.returncode != 0:
                continue
            base_sha = merge_result.stdout.strip()
            # Get file content at merge-base
            show_result = subprocess.run(
                ["git", "show", f"{base_sha}:{rel_path}"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if show_result.returncode != 0:
                return False  # file didn't exist at base — violation is new
            base_lines = show_result.stdout.count("\n")
            return base_lines > max_lines
        except (subprocess.TimeoutExpired, OSError):
            continue
    return False


def _check_sensitive_files(full_path: Path, rel_path: str, patterns: list[str]) -> list[Violation]:
    """Flag sensitive files that should not be committed."""
    filename = full_path.name
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(rel_path, pattern):
            return [
                Violation(
                    rule="sensitive_file",
                    file=rel_path,
                    message=f"{rel_path} matches sensitive pattern '{pattern}'.",
                    remediation=(
                        f"Add `{pattern}` to .gitignore and remove from staging: "
                        f"`git reset HEAD {rel_path}`. Use environment variables instead."
                    ),
                )
            ]
    return []


def _check_import_boundary(
    project_root: Path,
    file_prefix: str,
    staged_files: list[str],
) -> list[Violation]:
    """Detect cross-module imports that violate module boundaries.

    Args:
        file_prefix: Relative path prefix for this module's files (e.g. "backend/"
            or "" for root modules). Derived from the module's configured path.
    """
    violations: list[Violation] = []
    # Derive module directory name from prefix for display and filtering
    module_dir = file_prefix.rstrip("/") if file_prefix else ""

    # Get all module directories (top-level dirs that aren't hidden/standard)
    other_modules: list[str] = []
    try:
        for item in project_root.iterdir():
            if (
                item.is_dir()
                and not item.name.startswith(".")
                and item.name != module_dir
                and item.name not in ("node_modules", "__pycache__", ".venv", "venv")
            ):
                other_modules.append(item.name)
    except OSError:
        return []

    if not other_modules:
        return []

    # Build import patterns for other modules
    import_patterns = [
        re.compile(rf"(?:from|import)\s+{re.escape(mod)}[\.\s/]") for mod in other_modules
    ]

    for filepath in staged_files:
        if file_prefix and not filepath.startswith(file_prefix):
            continue

        full_path = project_root / filepath
        if not full_path.is_file() or full_path.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx"):
            continue

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for i, pattern in enumerate(import_patterns):
            if pattern.search(content):
                other_mod = other_modules[i]
                violations.append(
                    Violation(
                        rule="import_boundary",
                        file=filepath,
                        message=(
                            f"{filepath} imports from `{other_mod}/`, violating module boundary."
                        ),
                        remediation=(
                            f"Use the CONTRACTS.md interface instead of direct imports. "
                            f"If `{module_dir or 'this module'}` needs data from `{other_mod}`, "
                            f"create a Cross-Module Request in STATUS.md."
                        ),
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_staged_files(project_root: Path, file_prefix: str = "") -> list[str]:
    """Get files changed by the agent, scoped to module by file_prefix.

    Tries in order:
    1. Staged but uncommitted files (git diff --cached)
    2. Files changed on the current branch vs main/master (git diff merge-base)
    3. Fallback: all tracked files

    Strategy 2 is critical because agents are instructed to commit + push,
    so by the time QA runs there are typically no staged files.

    Args:
        file_prefix: Relative path prefix (e.g. "backend/" or "" for all files).
    """

    def _filter(files: list[str]) -> list[str]:
        if file_prefix:
            return [f for f in files if f.startswith(file_prefix)]
        return files

    # Strategy 1: staged but uncommitted files
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _filter(result.stdout.strip().splitlines())
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Strategy 2: files changed on branch vs main/master
    # The agent commits before QA runs, so we need to diff against base branch.
    for base in ("main", "master"):
        try:
            merge_base = subprocess.run(
                ["git", "merge-base", base, "HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if merge_base.returncode != 0:
                continue
            base_sha = merge_base.stdout.strip()
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{base_sha}..HEAD"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return _filter(result.stdout.strip().splitlines())
        except (subprocess.TimeoutExpired, OSError):
            pass

    # Strategy 3 fallback: all tracked files (last resort)
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return _filter(result.stdout.strip().splitlines())
    except (subprocess.TimeoutExpired, OSError):
        pass

    return []


def _module_file_prefix(
    project_root: Path, module_name: str, module_path: str | None = None
) -> str:
    """Compute the git-relative file prefix for a module.

    When module_path is the project root (path: ./), returns "" so all files
    are included. Otherwise returns "relative_dir/" as a prefix filter.
    """
    if module_name in ("root", "*"):
        return ""
    if module_path:
        try:
            rel = Path(module_path).relative_to(project_root)
            rel_str = str(rel)
            if rel_str == ".":
                return ""
            return rel_str.rstrip("/") + "/"
        except ValueError:
            pass
    if module_name:
        return module_name + "/"
    return ""


# ---------------------------------------------------------------------------
# Gate registration
# ---------------------------------------------------------------------------


@register("structural_check")
class StructuralCheckGate:
    """QA gate for structural lint checks."""

    def check(
        self,
        params: dict[str, Any] | None = None,
        project_root: Path | None = None,
        module_name: str = "",
        task_output: str = "",
        **kwargs: Any,
    ) -> QAResult:
        if project_root is None:
            return QAResult(
                gate="structural_check",
                passed=False,
                output="No project root provided.",
            )

        # Build config from params
        config = StructuralCheckConfig()
        if params:
            if "max_file_lines" in params:
                config.max_file_lines = int(params["max_file_lines"])
            if "enforce_module_boundary" in params:
                config.enforce_module_boundary = bool(params["enforce_module_boundary"])
            if "sensitive_patterns" in params:
                config.sensitive_patterns = params["sensitive_patterns"]

        resolved = kwargs.get("module_path")
        passed, violations = run_structural_check(
            project_root, module_name, config, module_path=resolved
        )

        return QAResult(
            gate="structural_check",
            passed=passed,
            output=_format_violations(violations),
            details={
                "violation_count": len(violations),
                "violations": [
                    {"rule": v.rule, "file": v.file, "message": v.message} for v in violations
                ],
            },
        )
