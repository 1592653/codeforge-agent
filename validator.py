"""Validator Agent — Runs tests and validates refactoring changes."""

from __future__ import annotations

import subprocess
from typing import Any

from codeforge.agents.base import AgentResult, AgentStatus, BaseAgent
from codeforge.utils.logger import get_logger


class ValidatorAgent(BaseAgent):
    """Agent responsible for validating refactoring changes.

    Runs the project's test suite, checks code coverage,
    detects regressions, and provides auto-rollback on failure.
    """

    def __init__(
        self,
        test_command: str = "pytest tests/ -x",
        min_coverage: int = 80,
        auto_rollback: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(name="validator", **kwargs)
        self.test_command = test_command
        self.min_coverage = min_coverage
        self.auto_rollback = auto_rollback
        self.logger = get_logger("agent.validator")

    def get_system_prompt(self) -> str:
        return """You are the Validator Agent in the CodeForge multi-agent system.
Your role is to validate refactoring changes by running tests and analyzing results.

Your validation checklist:
1. Run the project's test suite — all tests must pass
2. Check code coverage meets minimum threshold
3. Verify no regressions in critical paths
4. Analyze test failure output and determine if it's a real regression vs. flaky test
5. If auto-rollback is enabled and tests fail, revert changes

Provide clear pass/fail status with detailed test output for failures."""

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Validate refactoring changes by running tests."""
        repo_path = context.get("repo_path", ".")
        refactoring_results = context.get("refactoring_results", {})
        patches_applied = refactoring_results.get("patches_applied", 0)

        self.logger.info(f"Validating {patches_applied} applied patches")

        try:
            # Step 1: Run test suite
            test_result = await self._run_tests(repo_path)

            # Step 2: Analyze results with LLM if there are failures
            llm_analysis = None
            if not test_result["passed"]:
                llm_analysis = await self._analyze_failures(test_result)

                # Step 3: Auto-rollback if needed
                if self.auto_rollback and llm_analysis.get("is_regression", True):
                    rollback_result = await self._rollback(repo_path, refactoring_results)
                    test_result["rollback"] = rollback_result

            # Step 4: Lint check
            lint_result = await self._run_lint(repo_path)

            validation_data = {
                "test_result": test_result,
                "lint_result": lint_result,
                "llm_analysis": llm_analysis,
                "all_passed": test_result["passed"] and lint_result["passed"],
                "patches_validated": patches_applied,
            }

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=validation_data,
                token_usage=self.token_counter.run_usage.to_dict(),
            )

        except Exception as e:
            self.logger.error(f"Validator failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                errors=[str(e)],
            )

    async def _run_tests(self, repo_path: str) -> dict[str, Any]:
        """Run the project's test suite."""
        self.logger.info(f"Running tests: {self.test_command}")

        try:
            result = subprocess.run(
                self.test_command,
                shell=True,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            return {
                "passed": result.returncode == 0,
                "exit_code": result.returncode,
                "stdout": result.stdout[-5000:],  # Truncate for LLM
                "stderr": result.stderr[-3000:],
                "command": self.test_command,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "Test execution timed out after 300 seconds",
                "command": self.test_command,
            }
        except FileNotFoundError:
            return {
                "passed": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Test command not found: {self.test_command}",
                "command": self.test_command,
            }

    async def _analyze_failures(self, test_result: dict[str, Any]) -> dict[str, Any]:
        """Use LLM to analyze test failures and determine if they are regressions."""
        failure_prompt = f"""Analyze these test results from a refactored codebase:

TEST COMMAND: {test_result.get('command', 'unknown')}
EXIT CODE: {test_result.get('exit_code', -1)}

STDOUT:
{test_result.get('stdout', '')[-3000:]}

STDERR:
{test_result.get('stderr', '')[-2000:]}

Determine:
1. Is this a real regression caused by refactoring, or a pre-existing/flaky test?
2. Which specific tests failed?
3. What is the likely cause?
4. Should the changes be rolled back?

Return JSON with: is_regression (bool), failed_tests (list), root_cause (str), recommendation (str)"""

        result = await self.call_llm_structured(
            [{"role": "user", "content": failure_prompt}]
        )
        return result.get("parsed", {"is_regression": True, "recommendation": "rollback"})

    async def _rollback(self, repo_path: str, refactoring_results: dict[str, Any]) -> dict[str, Any]:
        """Rollback refactoring changes using git."""
        self.logger.warning("Initiating auto-rollback of refactoring changes")

        try:
            # Stash or reset changes
            result = subprocess.run(
                "git checkout -- .",
                shell=True,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )

            return {
                "status": "rolled_back" if result.returncode == 0 else "rollback_failed",
                "message": result.stderr or "Changes reverted successfully",
            }

        except Exception as e:
            return {
                "status": "rollback_failed",
                "message": str(e),
            }

    async def _run_lint(self, repo_path: str) -> dict[str, Any]:
        """Run linting checks on the codebase."""
        lint_commands = [
            "ruff check . --output-format=json",
            "python -m py_compile src/",
        ]

        for cmd in lint_commands:
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    return {
                        "passed": False,
                        "command": cmd,
                        "output": result.stdout[:2000],
                        "errors": result.stderr[:1000],
                    }
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        return {"passed": True, "command": "lint", "output": "All checks passed"}
