"""Layer check gate — enforce intra-module layer ordering.

Parses ARCHITECTURE.md for layer definitions (e.g. "models → schemas → services → routes → main")
and verifies that imports within a module respect the layer hierarchy:
layer[i] may only import from layer[j] where j <= i (same layer or lower).

Shared directories (utils/, shared/, common/) are treated as layer -1 (importable by all).
Test directories are excluded from checking.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import LayerCheckConfig
from ..models import QAResult
from . import register
from .structural_check import Violation


@dataclass
class LayerDef:
    """Layer definition for a single module."""

    module: str
    layers: list[str]  # ["models", "schemas", "services", "routes", "main"]


# Directories exempt from layer checking (treated as layer -1)
_EXEMPT_DIRS = {"utils", "shared", "common", "lib", "helpers", "core"}


def _parse_architecture_layers(project_root: Path, module_name: str) -> LayerDef | None:
    """Parse ARCHITECTURE.md for layer definitions of a specific module.

    Looks for patterns like:
        - **backend/**: models → schemas → services → routes → main
    """
    arch_path = project_root / "ARCHITECTURE.md"
    if not arch_path.exists():
        return None

    try:
        content = arch_path.read_text(encoding="utf-8")
    except OSError:
        return None

    # Match: - **module_name/**: layer1 → layer2 → layer3
    # Also handle unicode arrow and ASCII arrow
    pattern = re.compile(
        r"-\s+\*\*" + re.escape(module_name) + r"/?\*\*:?\s*(.+)",
        re.IGNORECASE,
    )

    for line in content.splitlines():
        m = pattern.search(line)
        if m:
            layer_str = m.group(1).strip()
            # Split on → or -> or ,
            layers = [
                s.strip().lower() for s in re.split(r"\s*(?:→|->|,)\s*", layer_str) if s.strip()
            ]
            if len(layers) >= 2:
                return LayerDef(module=module_name, layers=layers)

    return None


def _resolve_layer(filepath: str, layers: list[str]) -> int | None:
    """Map a file path to its layer index.

    Returns the layer index (0-based) or None if the file cannot be resolved.
    Returns -1 for exempt directories (utils/, shared/, common/).
    """
    parts = Path(filepath).parts

    for part in parts:
        part_lower = part.lower()

        # Check exempt directories
        if part_lower in _EXEMPT_DIRS:
            return -1

        # Check against defined layers
        for idx, layer in enumerate(layers):
            if part_lower == layer or part_lower.startswith(layer):
                return idx

    # Check filename (without extension) against layers
    stem = Path(filepath).stem.lower()
    for idx, layer in enumerate(layers):
        if stem == layer or stem.startswith(layer):
            return idx

    return None


def _extract_intra_imports(filepath: Path, module_prefix: str) -> list[str]:
    """Extract intra-module import targets from a Python/JS/TS file.

    Returns list of import path strings that reference the same module.
    """
    if not filepath.is_file():
        return []

    suffix = filepath.suffix
    if suffix not in (".py", ".ts", ".tsx", ".js", ".jsx"):
        return []

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    imports: list[str] = []

    if suffix == ".py":
        # Match: from module.sub import X  or  import module.sub
        for m in re.finditer(
            r"(?:from|import)\s+([\w.]+)",
            content,
        ):
            imp = m.group(1)
            # Filter to intra-module imports
            prefix_dot = module_prefix.replace("/", ".")
            if imp.startswith(prefix_dot) or imp.startswith(module_prefix.rstrip("/")):
                imports.append(imp)
    else:
        # JS/TS relative imports: from './services/foo' or from '../models/bar'
        for m in re.finditer(
            r"""(?:from|import)\s+['"](\.[^'"]+)['"]""",
            content,
        ):
            imports.append(m.group(1))

    return imports


def _check_layer_violations(
    project_root: Path,
    module_name: str,
    layer_def: LayerDef,
    files: list[str],
    file_prefix: str = "",
) -> list[Violation]:
    """Check files for layer ordering violations.

    layer[i] may only import from layer[j] where j <= i.

    Args:
        file_prefix: Git-relative prefix for this module's files (e.g. "backend/"
            or "" for root modules).
    """
    violations: list[Violation] = []
    # Use file_prefix if provided, otherwise fall back to module_name
    effective_prefix = file_prefix if file_prefix is not None else (module_name + "/")

    for filepath in files:
        # Skip files outside this module
        if effective_prefix and not filepath.startswith(effective_prefix):
            continue

        # Skip test files
        rel_in_module = filepath[len(effective_prefix) :] if effective_prefix else filepath
        parts = Path(rel_in_module).parts
        if any(p in ("tests", "test", "__tests__", "spec") for p in parts):
            continue

        full_path = project_root / filepath

        # Determine this file's layer
        source_layer = _resolve_layer(rel_in_module, layer_def.layers)
        if source_layer is None:
            continue  # Unknown layer, skip

        # Extract imports
        imports = _extract_intra_imports(full_path, effective_prefix)
        if not imports:
            continue

        for imp in imports:
            # Resolve the import target's layer
            if imp.startswith("./") or imp.startswith("../"):
                # JS/TS relative import: strip leading ./ or ../
                imp_path = re.sub(r"^(\.\./|\./)+", "", imp)
            else:
                # Python dotted import: convert dots to slashes
                imp_path = imp.replace(".", "/")
                # Remove module prefix if present
                mod_prefix = effective_prefix.rstrip("/") if effective_prefix else module_name
                if mod_prefix and imp_path.startswith(mod_prefix + "/"):
                    imp_path = imp_path[len(mod_prefix) + 1 :]

            target_layer = _resolve_layer(imp_path, layer_def.layers)
            if target_layer is None:
                continue  # Unknown target layer, skip

            # Exempt layers (-1) can be imported by anyone
            if target_layer == -1:
                continue

            # Violation: importing from a higher layer
            if target_layer > source_layer:
                source_name = layer_def.layers[source_layer] if source_layer >= 0 else "shared"
                target_name = layer_def.layers[target_layer]
                violations.append(
                    Violation(
                        rule="layer_violation",
                        file=filepath,
                        message=(
                            f"{filepath}: `{source_name}` layer imports from "
                            f"`{target_name}` layer (higher). "
                            f"Layer order: {' → '.join(layer_def.layers)}"
                        ),
                        remediation=(
                            f"Move the shared logic to `{source_name}/` or lower, "
                            f"or use dependency injection. "
                            f"`{source_name}` should not depend on `{target_name}`."
                        ),
                    )
                )

    return violations


def _get_staged_files(project_root: Path, file_prefix: str = "") -> list[str]:
    """Get git staged files, scoped to module by file_prefix.

    Args:
        file_prefix: Relative path prefix (e.g. "backend/" or "" for all files).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            files = result.stdout.strip().splitlines()
            if file_prefix:
                return [f for f in files if f.startswith(file_prefix)]
            return files
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: list tracked files
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            files = result.stdout.strip().splitlines()
            if file_prefix:
                return [f for f in files if f.startswith(file_prefix)]
            return files
    except (subprocess.TimeoutExpired, OSError):
        pass

    return []


def _format_violations(violations: list[Violation]) -> str:
    """Format layer violations into a human/agent-readable report."""
    if not violations:
        return "All layer checks passed."

    parts = [f"**{len(violations)} layer violation(s):**\n"]
    for v in violations:
        parts.append(f"VIOLATION [{v.rule}]: {v.message}")
        parts.append(f"FIX: {v.remediation}\n")

    return "\n".join(parts)


@register("layer_check")
class LayerCheckGate:
    """QA gate for intra-module layer ordering checks."""

    def check(
        self,
        params: dict[str, Any] | None = None,
        project_root: Path | None = None,
        module_name: str = "",
        task_output: str = "",
        **kwargs,
    ) -> QAResult:
        if project_root is None:
            return QAResult(
                gate="layer_check",
                passed=False,
                output="No project root provided.",
            )

        # Parse config from params
        config = LayerCheckConfig()
        if params:
            if "enabled" in params:
                config.enabled = bool(params["enabled"])
            if "unknown_file_policy" in params:
                config.unknown_file_policy = params["unknown_file_policy"]

        if not config.enabled:
            return QAResult(
                gate="layer_check",
                passed=True,
                output="Layer check disabled.",
            )

        # Compute file prefix from resolved module path
        from .structural_check import _module_file_prefix

        resolved = kwargs.get("module_path")
        file_prefix = _module_file_prefix(project_root, module_name, resolved)

        # Parse layer definition from ARCHITECTURE.md
        layer_def = _parse_architecture_layers(project_root, module_name)
        if layer_def is None:
            return QAResult(
                gate="layer_check",
                passed=True,
                output=f"No layer definition found for module '{module_name}' in ARCHITECTURE.md.",
            )

        # Get files to check
        files = _get_staged_files(project_root, file_prefix)
        if not files:
            return QAResult(
                gate="layer_check",
                passed=True,
                output="No files to check.",
            )

        # Run layer violation checks
        violations = _check_layer_violations(
            project_root, module_name, layer_def, files, file_prefix=file_prefix
        )

        return QAResult(
            gate="layer_check",
            passed=len(violations) == 0,
            output=_format_violations(violations),
            details={
                "violation_count": len(violations),
                "violations": [
                    {"rule": v.rule, "file": v.file, "message": v.message} for v in violations
                ],
                "layers": layer_def.layers,
            },
        )
