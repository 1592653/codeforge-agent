"""Scanner Agent — Discovers and filters code files for analysis."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from codeforge.agents.base import AgentResult, AgentStatus, BaseAgent
from codeforge.utils.logger import get_logger


class ScannerAgent(BaseAgent):
    """Agent responsible for scanning repositories and identifying code files.

    Performs intelligent file discovery using:
    - Glob pattern matching
    - Git diff analysis for change detection
    - File size and type filtering
    - Preliminary pattern matching for code smells
    """

    def __init__(
        self,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_file_size_kb: int = 500,
        **kwargs: Any,
    ) -> None:
        super().__init__(name="scanner", **kwargs)
        self.include_patterns = include_patterns or [
            "*.py", "*.js", "*.ts", "*.jsx", "*.tsx", "*.java", "*.go",
        ]
        self.exclude_patterns = exclude_patterns or [
            "**/test/**", "**/tests/**", "**/vendor/**",
            "**/node_modules/**", "**/__pycache__/**", "**/.git/**",
        ]
        self.max_file_size_kb = max_file_size_kb
        self.logger = get_logger("agent.scanner")

    def get_system_prompt(self) -> str:
        return """You are the Scanner Agent in the CodeForge multi-agent system.
Your role is to analyze repository structure and identify files that need code review.
Focus on: recently changed files, files with high complexity indicators,
files matching anti-pattern signatures, and files exceeding size thresholds.
Provide structured output with file paths and preliminary indicators."""

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Scan the repository for files to analyze."""
        repo_path = context.get("repo_path", ".")
        strategy = context.get("strategy", "full")  # full | incremental | diff

        self.logger.info(f"Scanning repository: {repo_path} (strategy: {strategy})")

        try:
            files = self._discover_files(repo_path, strategy)

            # Preliminary analysis with LLM for pattern detection
            llm_result = await self._analyze_patterns_with_llm(files[:50])

            scan_data = {
                "total_files_scanned": len(files),
                "files": [
                    {
                        "path": str(f.relative_to(repo_path)),
                        "size_kb": f.stat().st_size / 1024,
                        "language": self._detect_language(f),
                    }
                    for f in files
                ],
                "patterns_detected": llm_result.get("patterns", []),
                "strategy": strategy,
                "repo_path": repo_path,
            }

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=scan_data,
                token_usage=self.token_counter.run_usage.to_dict(),
            )

        except Exception as e:
            self.logger.error(f"Scanner failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                errors=[str(e)],
            )

    def _discover_files(self, repo_path: str, strategy: str) -> list[Path]:
        """Discover files matching inclusion/exclusion patterns."""
        root = Path(repo_path)
        if not root.exists():
            raise FileNotFoundError(f"Repository path not found: {repo_path}")

        files: list[Path] = []

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if self._should_exclude(file_path):
                continue
            if not self._matches_include(file_path):
                continue
            if file_path.stat().st_size > self.max_file_size_kb * 1024:
                continue
            files.append(file_path)

        self.logger.info(f"Discovered {len(files)} files matching criteria")
        return files

    def _should_exclude(self, path: Path) -> bool:
        """Check if a file should be excluded."""
        path_str = str(path)
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _matches_include(self, path: Path) -> bool:
        """Check if a file matches inclusion patterns."""
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(path.name, pattern):
                return True
        return False

    def _detect_language(self, path: Path) -> str:
        """Detect programming language from file extension."""
        ext_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
            ".go": "go", ".rs": "rust", ".rb": "ruby", ".cpp": "cpp",
            ".c": "c", ".cs": "csharp", ".php": "php", ".swift": "swift",
        }
        return ext_map.get(path.suffix, "unknown")

    async def _analyze_patterns_with_llm(self, files: list[Path]) -> dict[str, Any]:
        """Use LLM to identify potential pattern issues from file listing."""
        if not files:
            return {"patterns": []}

        file_listing = "\n".join(
            f"- {f.name} ({f.stat().st_size / 1024:.1f}KB)"
            for f in files[:30]
        )

        messages = [
            {
                "role": "user",
                "content": (
                    f"Analyze this file listing from a repository and identify potential "
                    f"code smell indicators based on naming patterns, file sizes, and structure:\n\n"
                    f"{file_listing}\n\n"
                    f"Return JSON with a 'patterns' array of objects with 'file', 'indicator', and 'reason' fields."
                ),
            }
        ]

        result = await self.call_llm_structured(messages)
        return result.get("parsed", {"patterns": []})
