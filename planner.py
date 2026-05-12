"""Planner Agent — Generates refactoring plans from analysis results."""

from __future__ import annotations

from typing import Any

from codeforge.agents.base import AgentResult, AgentStatus, BaseAgent
from codeforge.utils.logger import get_logger

REFACTORING_PATTERNS = {
    "extract_method": {
        "description": "Extract a code fragment into a new named method",
        "applicable_when": ["long_method", "duplicate_code"],
        "risk": "low",
    },
    "extract_class": {
        "description": "Move a group of related methods/fields into a new class",
        "applicable_when": ["god_class", "feature_envy"],
        "risk": "medium",
    },
    "introduce_parameter_object": {
        "description": "Replace multiple parameters with an object",
        "applicable_when": ["too_many_parameters"],
        "risk": "low",
    },
    "replace_conditional_with_polymorphism": {
        "description": "Replace complex conditional logic with polymorphic dispatch",
        "applicable_when": ["high_complexity", "nested_conditionals"],
        "risk": "medium",
    },
    "move_method": {
        "description": "Move a method to a more appropriate class",
        "applicable_when": ["feature_envy", "low_cohesion"],
        "risk": "medium",
    },
    "simplify_conditional": {
        "description": "Simplify complex conditional expressions",
        "applicable_when": ["high_complexity"],
        "risk": "low",
    },
}


class PlannerAgent(BaseAgent):
    """Agent responsible for generating refactoring plans.

    Takes analysis results and produces a prioritized, actionable
    refactoring plan with risk assessment and impact estimates.
    """

    def __init__(self, max_changes_per_run: int = 20, **kwargs: Any) -> None:
        super().__init__(name="planner", **kwargs)
        self.max_changes_per_run = max_changes_per_run
        self.logger = get_logger("agent.planner")

    def get_system_prompt(self) -> str:
        return """You are the Planner Agent in the CodeForge multi-agent system.
Your role is to synthesize code analysis results into a concrete, prioritized refactoring plan.

For each refactoring task, specify:
1. Target file and function/class
2. Refactoring pattern to apply (Extract Method, Extract Class, etc.)
3. Specific instructions for the Refactorer Agent
4. Risk level (low/medium/high) and potential impact radius
5. Dependencies between tasks (ordering constraints)

Prioritize by: impact (high complexity reduction) × feasibility (low risk).
Never exceed the maximum changes per run limit.
Always consider test coverage — higher-risk changes need more validation."""

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Generate a refactoring plan from analysis results."""
        analysis = context.get("analysis", {})
        strategy = context.get("strategy", "incremental")

        self.logger.info(f"Generating refactoring plan (strategy: {strategy})")

        try:
            code_smells = analysis.get("code_smells", [])
            complexity_summary = analysis.get("complexity_summary", {})
            semantic_issues = analysis.get("semantic_issues", [])

            # Generate plan using LLM
            plan = await self._generate_plan(
                code_smells, complexity_summary, semantic_issues, strategy
            )

            # Apply safety limits
            plan = self._apply_safety_limits(plan)

            plan_data = {
                "strategy": strategy,
                "total_tasks": len(plan["tasks"]),
                "tasks": plan["tasks"],
                "estimated_impact": plan.get("estimated_impact", {}),
                "risk_summary": plan.get("risk_summary", {}),
                "execution_order": plan.get("execution_order", []),
            }

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=plan_data,
                token_usage=self.token_counter.run_usage.to_dict(),
            )

        except Exception as e:
            self.logger.error(f"Planner failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                errors=[str(e)],
            )

    async def _generate_plan(
        self,
        code_smells: list[dict[str, Any]],
        complexity_summary: dict[str, Any],
        semantic_issues: list[dict[str, Any]],
        strategy: str,
    ) -> dict[str, Any]:
        """Use LLM to generate a comprehensive refactoring plan."""
        context_text = self._build_context(code_smells, complexity_summary, semantic_issues)

        planning_prompt = f"""Based on the following code analysis, generate a refactoring plan.

ANALYSIS CONTEXT:
{context_text}

STRATEGY: {strategy}
MAX CHANGES: {self.max_changes_per_run}

AVAILABLE PATTERNS:
{self._format_patterns()}

Generate a JSON plan with:
- "tasks": array of refactoring tasks, each with: id, file, target, pattern, description, risk, priority
- "execution_order": ordered list of task IDs (respecting dependencies)
- "estimated_impact": expected improvements (complexity_reduction, readability_score)
- "risk_summary": counts by risk level

Focus on highest-impact, lowest-risk changes first for '{strategy}' strategy."""

        result = await self.call_llm_structured(
            [{"role": "user", "content": planning_prompt}]
        )
        return result.get("parsed", {"tasks": []})

    def _build_context(
        self,
        code_smells: list[dict[str, Any]],
        complexity_summary: dict[str, Any],
        semantic_issues: list[dict[str, Any]],
    ) -> str:
        """Build context string for the planning prompt."""
        parts = []

        if complexity_summary:
            parts.append(
                f"Files analyzed: {complexity_summary.get('total_files', 0)}, "
                f"Functions: {complexity_summary.get('total_functions', 0)}, "
                f"Avg complexity: {complexity_summary.get('average_complexity', 0)}"
            )

        if code_smells:
            parts.append(f"Code smells detected ({len(code_smells)}):")
            for smell in code_smells[:15]:
                parts.append(
                    f"  - [{smell.get('severity', 'info')}] {smell.get('type', 'unknown')}: "
                    f"{smell.get('name', 'N/A')} in {smell.get('file', 'N/A')}"
                )

        if semantic_issues:
            parts.append(f"Semantic issues ({len(semantic_issues)}):")
            for issue in semantic_issues[:10]:
                parts.append(f"  - {issue}")

        return "\n".join(parts) if parts else "No analysis data available."

    def _format_patterns(self) -> str:
        """Format available refactoring patterns."""
        lines = []
        for name, info in REFACTORING_PATTERNS.items():
            lines.append(f"- {name}: {info['description']} (risk: {info['risk']})")
        return "\n".join(lines)

    def _apply_safety_limits(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Apply safety limits to the plan."""
        tasks = plan.get("tasks", [])

        # Limit total changes
        if len(tasks) > self.max_changes_per_run:
            # Sort by priority and take top N
            tasks.sort(key=lambda t: t.get("priority", 999))
            tasks = tasks[: self.max_changes_per_run]
            plan["tasks"] = tasks
            self.logger.warning(
                f"Plan truncated to {self.max_changes_per_run} tasks (safety limit)"
            )

        return plan
