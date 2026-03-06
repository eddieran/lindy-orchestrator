"""Remediation-rich QA feedback formatting.

Transforms raw QA output (pytest failures, ruff violations, tsc errors)
into structured remediation messages that teach the agent HOW to fix issues,
not just WHAT failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


def format_qa_feedback(gate: str, raw_output: str) -> str:
    """Transform raw QA output into structured remediation.

    Dispatches to a gate-specific parser, falling back to a generic
    truncated output with guidance.
    """
    if not raw_output:
        return f"Gate `{gate}` failed with no output."

    # Try gate-specific parsers
    for pattern, parser in _PARSERS:
        if re.search(pattern, gate, re.IGNORECASE):
            return parser(raw_output)

    # Generic fallback
    return _parse_generic(raw_output)


# ---------------------------------------------------------------------------
# Gate-specific parsers
# ---------------------------------------------------------------------------


def _parse_pytest(raw: str) -> str:
    """Parse pytest output into structured failure descriptions."""
    failures: list[str] = []

    # Match FAILED lines: FAILED tests/test_foo.py::test_bar - AssertionError: ...
    failed_pattern = re.compile(r"FAILED\s+([\w/\\.]+::\w+)(?:\s*-\s*(.+))?", re.MULTILINE)
    for match in failed_pattern.finditer(raw):
        test_path = match.group(1)
        reason = match.group(2) or "unknown"
        failures.append(f"  - `{test_path}`: {reason}")

    # Match assertion lines: E       assert X == Y
    assertion_pattern = re.compile(r"^E\s+(.+)$", re.MULTILINE)
    assertions = assertion_pattern.findall(raw)

    # Match short test summary
    summary_match = re.search(r"=+ short test summary info =+\n((?:FAILED .+\n?)+)", raw)

    if failures:
        parts = ["**pytest failures:**"]
        parts.extend(failures[:10])  # Cap at 10 failures
        if assertions:
            parts.append("\n**Key assertions:**")
            for a in assertions[:5]:
                parts.append(f"  - {a.strip()}")
        parts.append(
            "\n**FIX:** Read each failing test, check the assertion, "
            "and verify your implementation matches the expected behavior."
        )
        return "\n".join(parts)

    # Fallback: extract short test summary if no FAILED lines parsed
    if summary_match:
        return (
            f"**pytest failures:**\n{summary_match.group(1).strip()}\n\n"
            f"**FIX:** Run `pytest -x` locally to see full tracebacks, "
            f"then fix the first failing test."
        )

    # Last resort: truncate
    return _truncate_with_guidance(raw, "pytest")


def _parse_ruff(raw: str) -> str:
    """Parse ruff/eslint lint output into actionable fixes."""
    # Match: file.py:10:5: E302 expected 2 blank lines, got 1
    violations: list[str] = []
    lint_pattern = re.compile(r"^([\w/\\.]+):(\d+):(\d+):\s+(\w+)\s+(.+)$", re.MULTILINE)
    for match in lint_pattern.finditer(raw):
        filepath = match.group(1)
        line = match.group(2)
        rule = match.group(4)
        message = match.group(5)
        violations.append(f"  - `{filepath}:{line}` [{rule}]: {message}")

    if violations:
        parts = [f"**Lint violations ({len(violations)}):**"]
        parts.extend(violations[:15])
        if len(violations) > 15:
            parts.append(f"  ... and {len(violations) - 15} more")
        parts.append(
            "\n**FIX:** Run `ruff check --fix .` for auto-fixable issues, "
            "then manually fix the rest."
        )
        return "\n".join(parts)

    return _truncate_with_guidance(raw, "linter")


def _parse_tsc(raw: str) -> str:
    """Parse TypeScript compiler errors into structured feedback."""
    # Match: src/file.ts(10,5): error TS2322: Type 'X' is not assignable to type 'Y'.
    errors: list[str] = []
    tsc_pattern = re.compile(
        r"^([\w/\\.]+)\((\d+),(\d+)\):\s+error\s+(TS\d+):\s+(.+)$", re.MULTILINE
    )
    for match in tsc_pattern.finditer(raw):
        filepath = match.group(1)
        line = match.group(2)
        code = match.group(4)
        message = match.group(5)
        errors.append(f"  - `{filepath}:{line}` [{code}]: {message}")

    if errors:
        parts = [f"**TypeScript errors ({len(errors)}):**"]
        parts.extend(errors[:15])
        if len(errors) > 15:
            parts.append(f"  ... and {len(errors) - 15} more")
        parts.append(
            "\n**FIX:** Check each file at the indicated line. "
            "Ensure types match the expected interfaces."
        )
        return "\n".join(parts)

    return _truncate_with_guidance(raw, "tsc")


def _parse_generic(raw: str) -> str:
    """Generic fallback: truncated output with guidance."""
    return _truncate_with_guidance(raw, "command")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_with_guidance(raw: str, tool_name: str) -> str:
    """Truncate raw output and add generic fix guidance."""
    # Keep last 1500 chars (usually more useful than first)
    if len(raw) > 2000:
        truncated = f"...(truncated {len(raw) - 1500} chars)...\n{raw[-1500:]}"
    else:
        truncated = raw

    return (
        f"**{tool_name} output:**\n```\n{truncated.strip()}\n```\n\n"
        f"**FIX:** Run the failing command locally to see the full output and debug."
    )


# Parser registry: (gate_name_pattern, parser_function)
_PARSERS: list[tuple[str, callable]] = [
    (r"pytest|py\.test|python.*test", _parse_pytest),
    (r"ruff|eslint|flake8|pylint", _parse_ruff),
    (r"tsc|typescript|tsc-check", _parse_tsc),
]


# ---------------------------------------------------------------------------
# Structured feedback (v0.4.0)
# ---------------------------------------------------------------------------


class FailureCategory(str, Enum):
    TEST_FAILURE = "test_failure"
    LINT_ERROR = "lint_error"
    TYPE_ERROR = "type_error"
    BUILD_ERROR = "build_error"
    BOUNDARY_VIOLATION = "boundary_violation"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class StructuredFeedback:
    category: FailureCategory
    summary: str
    specific_errors: list[str] = field(default_factory=list)
    remediation_steps: list[str] = field(default_factory=list)
    files_to_check: list[str] = field(default_factory=list)
    retry_number: int = 0


def classify_failure(gate: str, raw_output: str) -> FailureCategory:
    """Classify a QA failure into a category based on gate name and output."""
    gate_lower = gate.lower()

    if re.search(r"pytest|py\.test|test", gate_lower):
        return FailureCategory.TEST_FAILURE
    if re.search(r"ruff|eslint|flake8|pylint|lint", gate_lower):
        return FailureCategory.LINT_ERROR
    if re.search(r"tsc|typescript|type.?check", gate_lower):
        return FailureCategory.TYPE_ERROR
    if re.search(r"structural|layer|boundary", gate_lower):
        return FailureCategory.BOUNDARY_VIOLATION
    if re.search(r"build|compile|make", gate_lower):
        return FailureCategory.BUILD_ERROR
    if re.search(r"timeout|stall", gate_lower):
        return FailureCategory.TIMEOUT

    # Check output content as fallback
    output_lower = raw_output.lower()
    if "FAILED" in raw_output and ("assert" in output_lower or "test" in output_lower):
        return FailureCategory.TEST_FAILURE
    if "error TS" in raw_output:
        return FailureCategory.TYPE_ERROR

    return FailureCategory.UNKNOWN


def _extract_file_paths(raw: str) -> list[str]:
    """Extract file paths mentioned in error output."""
    patterns = [
        re.compile(r"([\w/\\]+\.(?:py|ts|tsx|js|jsx)):\d+", re.MULTILINE),
        re.compile(r"([\w/\\]+\.(?:py|ts|tsx|js|jsx))\(\d+", re.MULTILINE),
        re.compile(r"FAILED\s+([\w/\\.]+)::", re.MULTILINE),
    ]
    files = set()
    for pattern in patterns:
        for match in pattern.finditer(raw):
            files.add(match.group(1))
    return sorted(files)[:10]


def _extract_specific_errors(raw: str, limit: int = 5) -> list[str]:
    """Extract the most specific error lines from raw output."""
    errors = []

    # pytest FAILED lines
    for m in re.finditer(r"FAILED\s+([\w/\\.]+::\w+)(?:\s*-\s*(.+))?", raw):
        errors.append(f"{m.group(1)}: {m.group(2) or 'failed'}")

    # Lint violations
    for m in re.finditer(r"^([\w/\\.]+:\d+:\d+:\s+\w+\s+.+)$", raw, re.MULTILINE):
        errors.append(m.group(1))

    # TypeScript errors
    for m in re.finditer(r"^([\w/\\.]+\(\d+,\d+\):\s+error\s+TS\d+:.+)$", raw, re.MULTILINE):
        errors.append(m.group(1))

    # Generic error lines
    if not errors:
        for m in re.finditer(r"(?:^|\n).*(?:Error|error|FAIL|fail).*$", raw, re.MULTILINE):
            line = m.group(0).strip()
            if line and len(line) < 200:
                errors.append(line)

    return errors[:limit]


def build_structured_feedback(
    gate: str, raw_output: str, retry_number: int = 0
) -> StructuredFeedback:
    """Build a StructuredFeedback from raw QA output."""
    category = classify_failure(gate, raw_output)
    specific = _extract_specific_errors(raw_output)
    files = _extract_file_paths(raw_output)

    remediation_map = {
        FailureCategory.TEST_FAILURE: [
            "Read each failing test and its assertion",
            "Verify your implementation matches expected behavior",
            "Run `pytest -x` to focus on the first failure",
        ],
        FailureCategory.LINT_ERROR: [
            "Run `ruff check --fix .` for auto-fixable issues",
            "Manually fix remaining violations at listed file:line locations",
        ],
        FailureCategory.TYPE_ERROR: [
            "Check each file at the indicated line",
            "Ensure types match the expected interfaces",
        ],
        FailureCategory.BOUNDARY_VIOLATION: [
            "You modified files outside your module directory",
            "Only modify files within your assigned module path",
            "Use CONTRACTS.md interfaces for cross-module communication",
        ],
        FailureCategory.BUILD_ERROR: [
            "Check build output for missing dependencies",
            "Verify import paths and module resolution",
        ],
        FailureCategory.TIMEOUT: [
            "Simplify your approach to avoid long-running operations",
            "Break complex tasks into smaller steps",
        ],
    }

    return StructuredFeedback(
        category=category,
        summary=specific[0] if specific else f"Gate `{gate}` failed",
        specific_errors=specific,
        remediation_steps=remediation_map.get(category, ["Review the error output and fix"]),
        files_to_check=files,
        retry_number=retry_number,
    )


def build_retry_prompt(
    original_prompt: str,
    feedback_history: list[StructuredFeedback],
    retry_number: int,
    max_retries: int,
) -> str:
    """Build a progressively focused retry prompt.

    Retry 1: Full original prompt + structured feedback
    Retry 2+: Simplified prompt focused only on failing files/errors
    """
    latest = feedback_history[-1] if feedback_history else None

    if retry_number <= 1 or not latest:
        # Full prompt + structured feedback
        parts = [original_prompt, "", "## IMPORTANT: Previous attempt failed QA verification", ""]
        for fb in feedback_history:
            parts.append(f"### {fb.category.value}")
            parts.append(f"**Summary:** {fb.summary}")
            if fb.specific_errors:
                parts.append("**Errors:**")
                for err in fb.specific_errors:
                    parts.append(f"  - {err}")
            if fb.files_to_check:
                parts.append(f"**Files to check:** {', '.join(fb.files_to_check)}")
            if fb.remediation_steps:
                parts.append("**Fix steps:**")
                for step in fb.remediation_steps:
                    parts.append(f"  - {step}")
            parts.append("")
        return "\n".join(parts)

    # Retry 2+: Focused prompt — skip original, focus on errors
    parts = [
        f"## RETRY {retry_number}/{max_retries} — Focus on fixing these specific errors:",
        "",
    ]
    if latest.specific_errors:
        for err in latest.specific_errors:
            parts.append(f"- {err}")
    if latest.files_to_check:
        parts.append(f"\nFocus on these files: {', '.join(latest.files_to_check)}")
    if latest.remediation_steps:
        parts.append("\nActions:")
        for step in latest.remediation_steps:
            parts.append(f"- {step}")
    parts.append(
        "\nDo NOT re-read the entire codebase. Go directly to the failing "
        "files and fix the specific errors listed above."
    )
    return "\n".join(parts)
