"""Tests for prompt templates — render_plan_prompt."""

from __future__ import annotations

from lindy_orchestrator.prompts import render_plan_prompt


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

    def test_branch_prefix_accepted(self):
        """branch_prefix param is accepted (used by scheduler at dispatch time, not in template)."""
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
            branch_prefix="feature",
        )
        # Branch delivery instructions are injected at dispatch time, not in the planning template
        assert "Goal" in result

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

    def test_enriched_task_fields_are_documented(self):
        result = render_plan_prompt(
            goal="Goal",
            module_summaries={"x": "y"},
        )
        assert '"generator_prompt"' in result
        assert '"acceptance_criteria"' in result
        assert '"evaluator_prompt"' in result
        assert "Every task MUST include all three fields below" in result


