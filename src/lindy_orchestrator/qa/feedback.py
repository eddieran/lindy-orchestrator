"""Remediation-rich QA feedback formatting.

Transforms raw QA output (pytest failures, ruff violations, tsc errors)
into structured remediation messages that teach the agent HOW to fix issues,
not just WHAT failed.
"""

from __future__ import annotations

import re


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
