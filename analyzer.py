"""Analyzer Agent — Deep code analysis with AST parsing and LLM reasoning."""

from __future__ import annotations

from typing import Any

from codeforge.agents.base import AgentResult, AgentStatus, BaseAgent
from codeforge.analyzers.ast_parser import ASTParser
from codeforge.analyzers.complexity import ComplexityAnalyzer
from codeforge.analyzers.dependency import DependencyGraphBuilder
from codeforge.utils.logger import get_logger


class AnalyzerAgent(BaseAgent):
    """Agent responsible for deep code analysis.

    Combines static analysis (AST, complexity metrics, dependency graphs)
    with LLM-powered semantic analysis for comprehensive code understanding.
    """

    def __init__(
        self,
        complexity_threshold: int = 15,
        max_function_lines: int = 50,
        **kwargs: Any,
    ) -> None:
        super().__init__(name="analyzer", **kwargs)
        self.ast_parser = ASTParser()
        self.complexity_analyzer = ComplexityAnalyzer(
            complexity_threshold=complexity_threshold,
            max_function_lines=max_function_lines,
        )
        self.logger = get_logger("agent.analyzer")

    def get_system_prompt(self) -> str:
        return """You are the Analyzer Agent in the CodeForge multi-agent system.
Your role is to perform deep code analysis combining static metrics with semantic understanding.

Analyze code for:
1. Cyclomatic complexity and maintainability issues
2. Design pattern violations and anti-patterns
3. Dependency coupling problems
4. Code duplication and redundancy
5. Security vulnerabilities
6. Performance bottlenecks

Provide structured analysis with:
- Severity levels (critical, warning, info)
- Specific file and line references
- Root cause explanations
- Impact assessment for each issue found"""

    async def execute(self, context: dict[str, Any]) -> AgentResult:
        """Perform deep analysis on scanned files."""
        repo_path = context.get("repo_path", ".")
        files = context.get("files", [])

        self.logger.info(f"Analyzing {len(files)} files in {repo_path}")

        try:
            # Phase 1: Static analysis
            complexity_reports = self._analyze_complexity(repo_path, files)
            dep_graph = self._build_dependency_graph(repo_path)

            # Phase 2: LLM-powered semantic analysis
            semantic_issues = await self._semantic_analysis(repo_path, files, complexity_reports)

            # Phase 3: Compile findings
            analysis_data = {
                "repo_path": repo_path,
                "total_files_analyzed": len(files),
                "complexity_summary": self._summarize_complexity(complexity_reports),
                "dependency_summary": {
                    "total_files": dep_graph.total_files,
                    "total_edges": dep_graph.total_edges,
                    "cycles_detected": len(dep_graph.cycles),
                    "external_packages": list(dep_graph.external_packages),
                    "most_depended_on": dep_graph.get_most_depended_on(5),
                    "most_dependent": dep_graph.get_most_dependent(5),
                },
                "semantic_issues": semantic_issues,
                "code_smells": self._collect_code_smells(complexity_reports),
                "total_issues": len(semantic_issues) + sum(
                    len(r.code_smells) for r in complexity_reports.values()
                ),
            }

            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.COMPLETED,
                data=analysis_data,
                token_usage=self.token_counter.run_usage.to_dict(),
            )

        except Exception as e:
            self.logger.error(f"Analyzer failed: {e}")
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                data={},
                errors=[str(e)],
            )

    def _analyze_complexity(
        self, repo_path: str, files: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Run complexity analysis on all files."""
        from pathlib import Path

        reports = {}
        root = Path(repo_path)

        for file_info in files:
            file_path = root / file_info.get("path", "")
            if not file_path.exists():
                continue
            try:
                report = self.complexity_analyzer.analyze_file(file_path)
                reports[file_info["path"]] = report
            except Exception:
                continue

        self.logger.info(f"Complexity analysis completed for {len(reports)} files")
        return reports

    def _build_dependency_graph(self, repo_path: str) -> Any:
        """Build the project dependency graph."""
        builder = DependencyGraphBuilder(repo_path)
        graph = builder.build()
        self.logger.info(
            f"Dependency graph: {graph.total_files} files, "
            f"{graph.total_edges} edges, {len(graph.cycles)} cycles"
        )
        return graph

    async def _semantic_analysis(
        self,
        repo_path: str,
        files: list[dict[str, Any]],
        complexity_reports: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Use LLM to perform semantic code analysis."""
        # Select top complex files for LLM analysis
        hot_spots = []
        for path, report in complexity_reports.items():
            if report.top_complex_functions:
                hot_spots.append({
                    "file": path,
                    "top_complexity": report.top_complex_functions[0][1] if report.top_complex_functions else 0,
                    "smells": len(report.code_smells),
                })

        hot_spots.sort(key=lambda x: x["top_complexity"], reverse=True)

        if not hot_spots:
            return []

        analysis_prompt = (
            "Analyze these code hotspots for deeper issues:\n\n"
            + "\n".join(
                f"- {h['file']}: complexity={h['top_complexity']}, smells={h['smells']}"
                for h in hot_spots[:10]
            )
            + "\n\nIdentify: design issues, potential bugs, performance concerns, "
              "and refactoring priorities. Return JSON with 'issues' array."
        )

        result = await self.call_llm_structured(
            [{"role": "user", "content": analysis_prompt}]
        )
        return result.get("parsed", {}).get("issues", [])

    def _summarize_complexity(self, reports: dict[str, Any]) -> dict[str, Any]:
        """Summarize complexity metrics across all files."""
        all_smells = []
        total_functions = 0
        avg_complexity = 0

        for report in reports.values():
            all_smells.extend(report.code_smells)
            total_functions += len(report.functions)
            if report.functions:
                avg_complexity += sum(
                    m.cyclomatic_complexity for m in report.functions.values()
                )

        if total_functions > 0:
            avg_complexity /= total_functions

        return {
            "total_files": len(reports),
            "total_functions": total_functions,
            "average_complexity": round(avg_complexity, 2),
            "total_smells": len(all_smells),
            "smells_by_type": self._count_smell_types(all_smells),
        }

    def _collect_code_smells(self, reports: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect all code smells from all reports."""
        all_smells = []
        for path, report in reports.items():
            for smell in report.code_smells:
                smell["file"] = path
                all_smells.append(smell)
        return all_smells

    @staticmethod
    def _count_smell_types(smells: list[dict[str, Any]]) -> dict[str, int]:
        """Count code smells by type."""
        counts: dict[str, int] = {}
        for smell in smells:
            smell_type = smell.get("type", "unknown")
            counts[smell_type] = counts.get(smell_type, 0) + 1
        return counts
