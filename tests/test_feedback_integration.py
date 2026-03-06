"""Tests for structured feedback, failure classification, and progressive retry prompts."""

from __future__ import annotations

from lindy_orchestrator.qa.feedback import (
    FailureCategory,
    StructuredFeedback,
    build_retry_prompt,
    build_structured_feedback,
    classify_failure,
)


class TestClassifyFailure:
    def test_pytest_gate(self):
        assert classify_failure("command_check_pytest", "FAILED") == FailureCategory.TEST_FAILURE

    def test_ruff_gate(self):
        assert classify_failure("ruff", "") == FailureCategory.LINT_ERROR

    def test_eslint_gate(self):
        assert classify_failure("eslint", "") == FailureCategory.LINT_ERROR

    def test_tsc_gate(self):
        assert classify_failure("tsc", "") == FailureCategory.TYPE_ERROR

    def test_typescript_gate(self):
        assert classify_failure("typescript-check", "") == FailureCategory.TYPE_ERROR

    def test_structural_check_gate(self):
        assert classify_failure("structural_check", "") == FailureCategory.BOUNDARY_VIOLATION

    def test_layer_check_gate(self):
        assert classify_failure("layer_check", "") == FailureCategory.BOUNDARY_VIOLATION

    def test_build_gate(self):
        assert classify_failure("build", "") == FailureCategory.BUILD_ERROR

    def test_timeout_gate(self):
        assert classify_failure("timeout", "") == FailureCategory.TIMEOUT

    def test_unknown_gate(self):
        assert classify_failure("custom_gate", "random output") == FailureCategory.UNKNOWN

    def test_fallback_pytest_from_output(self):
        output = "FAILED tests/test_foo.py::test_bar - AssertionError: assert 1 == 2"
        assert classify_failure("command_check", output) == FailureCategory.TEST_FAILURE

    def test_fallback_tsc_from_output(self):
        output = "src/app.ts(10,5): error TS2322: Type 'string' is not assignable to type 'number'."
        assert classify_failure("command_check", output) == FailureCategory.TYPE_ERROR


class TestBuildStructuredFeedback:
    def test_pytest_feedback(self):
        output = (
            "FAILED tests/test_api.py::test_create_user - AssertionError: assert 200 == 201\n"
            "FAILED tests/test_api.py::test_delete_user - KeyError: 'id'"
        )
        fb = build_structured_feedback("pytest", output, retry_number=1)
        assert fb.category == FailureCategory.TEST_FAILURE
        assert len(fb.specific_errors) >= 2
        assert "tests/test_api.py" in fb.files_to_check[0]
        assert fb.retry_number == 1
        assert len(fb.remediation_steps) > 0

    def test_lint_feedback(self):
        output = "src/app.py:10:5: E302 expected 2 blank lines, got 1\nsrc/app.py:20:1: F401 unused import"
        fb = build_structured_feedback("ruff", output)
        assert fb.category == FailureCategory.LINT_ERROR
        assert len(fb.specific_errors) >= 2
        assert "src/app.py" in fb.files_to_check

    def test_boundary_feedback(self):
        fb = build_structured_feedback("structural_check", "Modified files outside module")
        assert fb.category == FailureCategory.BOUNDARY_VIOLATION
        assert (
            "module" in fb.remediation_steps[0].lower()
            or "modified" in fb.remediation_steps[0].lower()
        )

    def test_empty_output(self):
        fb = build_structured_feedback("unknown", "")
        assert fb.category == FailureCategory.UNKNOWN
        assert fb.summary.startswith("Gate")

    def test_files_extracted(self):
        output = "Error in src/service.py:42\nAlso in src/model.py:10"
        fb = build_structured_feedback("build", output)
        assert "src/service.py" in fb.files_to_check
        assert "src/model.py" in fb.files_to_check


class TestBuildRetryPrompt:
    def test_retry_1_includes_full_prompt(self):
        fb = StructuredFeedback(
            category=FailureCategory.TEST_FAILURE,
            summary="test_foo failed",
            specific_errors=["FAILED test_foo - assert 1 == 2"],
            remediation_steps=["Fix the test"],
            files_to_check=["test_foo.py"],
        )
        prompt = build_retry_prompt(
            original_prompt="Add user API",
            feedback_history=[fb],
            retry_number=1,
            max_retries=3,
        )
        assert "Add user API" in prompt  # original preserved
        assert "test_foo failed" in prompt
        assert "test_foo.py" in prompt
        assert "QA verification" in prompt

    def test_retry_2_is_simplified(self):
        fb = StructuredFeedback(
            category=FailureCategory.TEST_FAILURE,
            summary="test_foo failed",
            specific_errors=["FAILED test_foo - assert 1 == 2"],
            remediation_steps=["Fix the test"],
            files_to_check=["test_foo.py"],
        )
        prompt = build_retry_prompt(
            original_prompt="Add user API (this is a very long prompt with lots of context)",
            feedback_history=[fb],
            retry_number=2,
            max_retries=3,
        )
        # Should NOT include original prompt
        assert "very long prompt" not in prompt
        # Should focus on errors
        assert "RETRY 2/3" in prompt
        assert "test_foo" in prompt
        assert "Do NOT re-read" in prompt

    def test_multiple_feedback_history(self):
        fb1 = StructuredFeedback(
            category=FailureCategory.LINT_ERROR,
            summary="lint errors",
            specific_errors=["E302 expected 2 blank lines"],
        )
        fb2 = StructuredFeedback(
            category=FailureCategory.TEST_FAILURE,
            summary="test still fails",
            specific_errors=["FAILED test_bar"],
            files_to_check=["bar.py"],
        )
        prompt = build_retry_prompt(
            original_prompt="Original",
            feedback_history=[fb1, fb2],
            retry_number=1,
            max_retries=3,
        )
        # Both feedbacks should appear
        assert "lint_error" in prompt
        assert "test_failure" in prompt

    def test_empty_feedback_history(self):
        prompt = build_retry_prompt(
            original_prompt="Do stuff",
            feedback_history=[],
            retry_number=1,
            max_retries=2,
        )
        assert "Do stuff" in prompt
        assert "QA verification" in prompt

    def test_retry_3_even_more_focused(self):
        fb = StructuredFeedback(
            category=FailureCategory.TEST_FAILURE,
            summary="still failing",
            specific_errors=["FAILED test_x"],
            remediation_steps=["Fix test_x"],
            files_to_check=["x.py"],
        )
        prompt = build_retry_prompt(
            original_prompt="Long original context",
            feedback_history=[fb],
            retry_number=3,
            max_retries=3,
        )
        assert "RETRY 3/3" in prompt
        assert "Long original" not in prompt
