"""Refactorer Agent — Generates and applies code refactoring patches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeforge.agents.base import AgentResult, AgentStatus, BaseAgent
from codeforge.utils.logger import get_logger


class RefactorerAgent(BaseAgent):
    """Agent responsible for executing refactoring tasks.

    Takes a refactoring plan and produces concrete code patches
    using LLM-powered code generation with safety constraints.
    """

    def __init__(
        self,
        auto_format: bool = True,
        create_branch: bool = True,
        branch_prefix: str = "codeforge/",
        **kwargs: Any,
    ) -> None:
        super().__init__(name="refactorer", **kwargs)
        self.auto_format = auto_format
        self.create_branch = create_branch
        self.branch_prefix = branch_prefix
        self.logger = get_logger("agent.refactorer")

    def get_system_prompt(self) -> str:
        return """You are the Refactorer Agent in the CodeForge multi-agent system.
Your role is to execute refactoring tasks by generating precise code patches.

Rules:
1. Preserve ALL existing functionality — never remove logic, only restructure
2. Maintain consistent code style with the existing codebase
3. Add type hints where missing (Python) or improve type annotations
4. Preserve all comments and docstrings unless they become inaccurate
5. Generate minimal, focused diffs — change only what is necessary
6. Include clear commit messages explaining the refactoring rationale

Output format: JSON with patches array, each containing:
- file_path: target file
- original_snippet: the code to replace
- refactored_snippet: the replacement code
- explanation: why this change improves the code
- commit_message: descriptive commit message"""

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Execute refactoring tasks from the plan."""
        tasks = context.get("tasks", [])
        repo_path = context.get("repo_path", ".")

        self.logger.info(f"Executing {len(tasks)} refactoring tasks")

        try:
            results = []
            patches_applied = 0
            patches_failed = 0

            for task in tasks:
                patch_result = await self._refactor_task(task, repo_path)
                results.append(patch_result)
                if patch_result["status"] == "applied":
                    patches_applied += 1
                else:
                    patches_failed += 1

            refactor_data = {
                "total_tasks": len(tasks),
                "patches_applied": patches_applied,
                "patches_failed": patches_failed,
                "results": results,
                "branch_name": f"{self.branch_prefix}refactor-{len(tasks)}-changes",
            }

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=refactor_data,
                token_usage=self.token_counter.run_usage.to_dict(),
            )

        except Exception as e:
            self.logger.error(f"Refactorer failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                errors=[str(e)],
            )

    async def _refactor_task(self, task: dict[str, Any], repo_path: str) -> dict[str, Any]:
        """Refactor a single task by generating and applying a patch."""
        task_id = task.get("id", "unknown")
        file_path = task.get("file", "")
        pattern = task.get("pattern", "")
        description = task.get("description", "")

        self.logger.info(f"Refactoring task {task_id}: {pattern} in {file_path}")

        # Read the target file
        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            return {
                "task_id": task_id,
                "status": "skipped",
                "reason": f"File not found: {file_path}",
            }

        try:
            source_code = full_path.read_text(encoding="utf-8")
        except Exception as e:
            return {
                "task_id": task_id,
                "status": "failed",
                "reason": f"Cannot read file: {e}",
            }

        # Generate refactoring patch using LLM
        patch = await self._generate_patch(source_code, task)

        if not patch:
            return {
                "task_id": task_id,
                "status": "skipped",
                "reason": "LLM could not generate a valid patch",
            }

        # Apply the patch
        try:
            refactored_code = self._apply_patch(source_code, patch)
            full_path.write_text(refactored_code, encoding="utf-8")
            self.logger.info(f"Applied patch for task {task_id}")

            return {
                "task_id": task_id,
                "status": "applied",
                "file": file_path,
                "explanation": patch.get("explanation", ""),
                "commit_message": patch.get("commit_message", f"refactor: {pattern} in {file_path}"),
            }

        except Exception as e:
            return {
                "task_id": task_id,
                "status": "failed",
                "reason": f"Patch application failed: {e}",
            }

    async def _generate_patch(
        self, source_code: str, task: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Generate a refactoring patch using LLM."""
        prompt = f"""Refactor the following code according to this task:

TASK: {task.get('description', 'No description')}
PATTERN: {task.get('pattern', 'unknown')}
FILE: {task.get('file', 'unknown')}
RISK: {task.get('risk', 'medium')}

CURRENT CODE:
```python
{source_code[:8000]}
```

Generate a JSON response with:
- "patched_code": the complete refactored file content
- "explanation": what was changed and why
- "commit_message": a clear commit message

IMPORTANT: Return the COMPLETE file content in patched_code, not just the changed portion."""

        result = await self.call_llm_structured(
            [{"role": "user", "content": prompt}],
            max_tokens=8192,
        )

        return result.get("parsed")

    def _apply_patch(self, original: str, patch: dict[str, Any]) -> str:
        """Apply a generated patch to the source code."""
        patched_code = patch.get("patched_code", "")
        if not patched_code:
            raise ValueError("Empty patched code")

        # Basic validation: patched code should not be dramatically shorter
        if len(patched_code) < len(original) * 0.5:
            raise ValueError(
                f"Patched code is too short ({len(patched_code)} vs {len(original)} chars). "
                "This might indicate code loss."
            )

        return patched_code
