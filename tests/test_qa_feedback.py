"""Tests for remediation-rich QA feedback formatting."""

from lindy_orchestrator.qa.feedback import format_qa_feedback


class TestPytestParser:
    def test_extracts_failed_tests(self):
        raw = """\
============================= test session starts ==============================
FAILED tests/test_auth.py::test_login - AssertionError: assert 401 == 200
FAILED tests/test_auth.py::test_register - TypeError: missing argument
FAILED tests/test_utils.py::test_hash - ValueError: invalid salt
========================= 3 failed, 10 passed ==============================
"""
        result = format_qa_feedback("pytest", raw)

        assert "pytest failures" in result
        assert "test_auth.py::test_login" in result
        assert "test_auth.py::test_register" in result
        assert "test_utils.py::test_hash" in result
        assert "FIX:" in result

    def test_extracts_assertions(self):
        raw = """\
    def test_status_code():
        response = client.get("/api/users")
>       assert response.status_code == 200
E       assert 404 == 200
E       +  where 404 = <Response [404]>.status_code

FAILED tests/test_api.py::test_status_code - assert 404 == 200
"""
        result = format_qa_feedback("pytest", raw)

        assert "assert 404 == 200" in result

    def test_short_summary_fallback(self):
        raw = """\
======= short test summary info =======
FAILED tests/test_one.py::test_a
FAILED tests/test_two.py::test_b
"""
        result = format_qa_feedback("pytest", raw)

        assert "test_one.py" in result
        assert "test_two.py" in result

    def test_empty_output(self):
        result = format_qa_feedback("pytest", "")
        assert "no output" in result.lower()


class TestRuffParser:
    def test_extracts_lint_violations(self):
        raw = """\
src/auth.py:10:5: E302 expected 2 blank lines, got 1
src/auth.py:25:1: F401 `os` imported but unused
src/models.py:3:1: I001 Import block is un-sorted or un-formatted
Found 3 errors.
"""
        result = format_qa_feedback("ruff", raw)

        assert "Lint violations (3)" in result
        assert "src/auth.py:10" in result
        assert "[E302]" in result
        assert "src/auth.py:25" in result
        assert "[F401]" in result
        assert "FIX:" in result
        assert "ruff check --fix" in result

    def test_eslint_gate_name(self):
        raw = """\
src/App.tsx:5:1: no-unused-vars 'foo' is defined but never used
"""
        result = format_qa_feedback("eslint", raw)
        # Should use the ruff/eslint parser
        assert "Lint violations" in result or "output" in result.lower()


class TestTscParser:
    def test_extracts_type_errors(self):
        raw = """\
src/components/Button.tsx(15,3): error TS2322: Type 'string' is not assignable to type 'number'.
src/hooks/useAuth.ts(42,10): error TS2345: Argument of type 'null' is not assignable to parameter of type 'User'.
"""
        result = format_qa_feedback("tsc", raw)

        assert "TypeScript errors (2)" in result
        assert "Button.tsx:15" in result
        assert "[TS2322]" in result
        assert "useAuth.ts:42" in result
        assert "[TS2345]" in result
        assert "FIX:" in result


class TestGenericParser:
    def test_unknown_gate_uses_generic(self):
        raw = "Some custom check failed: missing configuration"
        result = format_qa_feedback("custom_check", raw)

        assert "command output" in result.lower()
        assert "missing configuration" in result
        assert "FIX:" in result

    def test_long_output_truncated(self):
        raw = "x" * 5000
        result = format_qa_feedback("unknown_gate", raw)

        assert "truncated" in result
        assert len(result) < 5000

    def test_gate_with_no_output(self):
        result = format_qa_feedback("any_gate", "")
        assert "no output" in result.lower()
