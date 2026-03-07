"""Tests for prompt templates — render_plan_prompt and render_report_prompt."""

from __future__ import annotations

from lindy_orchestrator.prompts import (
    REPORT_PROMPT_TEMPLATE,
    render_plan_prompt,
    render_report_prompt,
)


class TestRenderPlanPrompt:
    def test_basic_render(self):
        result = render_plan_prompt(
            goal="Add dark mode",
            module_summaries={"frontend": "Healthy, no blockers"},
        )
        assert "Add dark mode" in result
        assert "frontend" in result
        assert "Healthy, no blockers" in result

    def test_uses_modules_list_when_provided(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"mod1": "status1"},
            modules=[
                {"name": "mod1", "path": "mod1/"},
                {"name": "mod2", "path": "mod2/"},
            ],
        )
        assert "**mod1/**" in result
        assert "**mod2/**" in result

    def test_falls_back_to_summary_keys_without_modules(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"alpha": "ok", "beta": "ok"},
        )
        assert "**alpha/**" in result
        assert "**beta/**" in result

    def test_project_name_substituted(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            project_name="my-project",
        )
        assert "my-project" in result

    def test_branch_prefix_substituted(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            branch_prefix="feature",
        )
        assert "feature/task-" in result

    def test_with_architecture(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            architecture="Three-tier architecture with API gateway",
        )
        assert "Architecture" in result
        assert "Three-tier architecture" in result

    def test_without_architecture(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            architecture=None,
        )
        assert "Architecture" not in result

    def test_custom_available_gates(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            available_gates=["my_gate", "other_gate"],
        )
        assert "`my_gate`" in result
        assert "`other_gate`" in result

    def test_default_gates_include_ci_check(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
        )
        assert "ci_check" in result
        assert "command_check" in result
        assert "agent_check" in result

    def test_date_present_in_output(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
        )
        # Should contain a date like YYYY-MM-DD
        import re

        assert re.search(r"\d{4}-\d{2}-\d{2}", result)

    def test_empty_module_summaries(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={},
        )
        assert "Goal" in result

    def test_multiple_module_statuses(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={
                "frontend": "GREEN, 2 active tasks",
                "backend": "YELLOW, 1 blocker",
                "infra": "RED, CI broken",
            },
        )
        assert "### frontend" in result
        assert "### backend" in result
        assert "### infra" in result


class TestRenderReportPrompt:
    def test_basic_render(self):
        result = render_report_prompt(
            goal="Deploy v2",
            task_results=[
                {
                    "id": 1,
                    "module": "backend",
                    "description": "Update API",
                    "status": "completed",
                    "qa_summary": "ci_check: PASS",
                    "result_preview": "All tests passed",
                }
            ],
        )
        assert "Deploy v2" in result
        assert "Task 1 [backend]" in result
        assert "Update API" in result
        assert "completed" in result

    def test_multiple_tasks(self):
        result = render_report_prompt(
            goal="Goal",
            task_results=[
                {"id": 1, "module": "a", "description": "first", "status": "completed"},
                {"id": 2, "module": "b", "description": "second", "status": "failed"},
            ],
        )
        assert "Task 1" in result
        assert "Task 2" in result

    def test_missing_optional_fields(self):
        result = render_report_prompt(
            goal="Goal",
            task_results=[
                {"id": 1, "module": "x", "description": "task", "status": "completed"},
            ],
        )
        assert "none" in result  # qa_summary defaults to "none"
        assert "N/A" in result  # result_preview defaults to "N/A"

    def test_empty_task_results(self):
        result = render_report_prompt(goal="Goal", task_results=[])
        assert "Goal" in result

    def test_report_template_structure(self):
        assert "GOAL COMPLETED" in REPORT_PROMPT_TEMPLATE
        assert "GOAL PAUSED" in REPORT_PROMPT_TEMPLATE
        assert "Summary" in REPORT_PROMPT_TEMPLATE
