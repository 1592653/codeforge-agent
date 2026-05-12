"""Code complexity analysis using cyclomatic complexity and other metrics."""

from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeforge.analyzers.ast_parser import ASTParser, FileAnalysis, FunctionInfo


@dataclass
class ComplexityMetrics:
    """Complexity metrics for a code unit (function, class, or file)."""

    cyclomatic_complexity: int = 1
    cognitive_complexity: int = 0
    halstead_volume: float = 0.0
    maintainability_index: float = 100.0
    lines_of_code: int = 0
    max_nesting_depth: int = 0
    parameter_count: int = 0

    @property
    def rating(self) -> str:
        """Return a letter rating based on cyclomatic complexity."""
        if self.cyclomatic_complexity <= 5:
            return "A"
        elif self.cyclomatic_complexity <= 10:
            return "B"
        elif self.cyclomatic_complexity <= 20:
            return "C"
        elif self.cyclomatic_complexity <= 30:
            return "D"
        else:
            return "F"

    @property
    def risk_level(self) -> str:
        """Return risk level based on maintainability."""
        if self.maintainability_index >= 20:
            return "low"
        elif self.maintainability_index >= 10:
            return "medium"
        else:
            return "high"


@dataclass
class FileComplexityReport:
    """Complexity report for an entire file."""

    file_path: str
    overall: ComplexityMetrics = field(default_factory=ComplexityMetrics)
    functions: dict[str, ComplexityMetrics] = field(default_factory=dict)
    classes: dict[str, ComplexityMetrics] = field(default_factory=dict)
    top_complex_functions: list[tuple[str, int]] = field(default_factory=list)
    code_smells: list[dict[str, Any]] = field(default_factory=list)


class ComplexityAnalyzer:
    """Analyze code complexity using AST-based metrics."""

    def __init__(self, complexity_threshold: int = 15, max_function_lines: int = 50) -> None:
        self.complexity_threshold = complexity_threshold
        self.max_function_lines = max_function_lines
        self.ast_parser = ASTParser()

    def analyze_file(self, file_path: str | Path) -> FileComplexityReport:
        """Analyze complexity of a Python file."""
        analysis = self.ast_parser.parse_file(file_path)
        return self._build_report(analysis)

    def analyze_source(self, source: str, file_path: str = "<string>") -> FileComplexityReport:
        """Analyze complexity of Python source code."""
        analysis = self.ast_parser.parse_source(source, file_path)
        return self._build_report(analysis)

    def _build_report(self, analysis: FileAnalysis) -> FileComplexityReport:
        """Build a complexity report from file analysis."""
        report = FileComplexityReport(file_path=analysis.path)

        # Analyze file-level complexity
        report.overall.lines_of_code = analysis.code_lines
        report.overall.maintainability_index = self._compute_maintainability(analysis)

        # Analyze functions
        all_functions: list[tuple[str, FunctionInfo, str | None]] = []
        for func in analysis.functions:
            all_functions.append((func.name, func, None))
        for cls in analysis.classes:
            for method in cls.methods:
                qualified_name = f"{cls.name}.{method.name}"
                all_functions.append((qualified_name, method, cls.name))

        for qualified_name, func, parent_class in all_functions:
            metrics = self._compute_function_complexity(func)
            report.functions[qualified_name] = metrics

            # Detect code smells
            smells = self._detect_function_smells(qualified_name, metrics)
            report.code_smells.extend(smells)

        # Analyze classes
        for cls in analysis.classes:
            cls_metrics = ComplexityMetrics(
                lines_of_code=cls.lines_of_code,
                cyclomatic_complexity=max(
                    (report.functions.get(f"{cls.name}.{m.name}", ComplexityMetrics()).cyclomatic_complexity
                     for m in cls.methods),
                    default=1,
                ),
            )
            report.classes[cls.name] = cls_metrics

            if cls.method_count > 20:
                report.code_smells.append({
                    "type": "god_class",
                    "name": cls.name,
                    "message": f"Class '{cls.name}' has {cls.method_count} methods (threshold: 20)",
                    "severity": "warning",
                })

        # Rank top complex functions
        report.top_complex_functions = sorted(
            [(name, m.cyclomatic_complexity) for name, m in report.functions.items()],
            key=lambda x: x[1],
            reverse=True,
        )[:10]

        return report

    def _compute_function_complexity(self, func: FunctionInfo) -> ComplexityMetrics:
        """Compute cyclomatic complexity for a function (simplified AST walk)."""
        # Simplified complexity calculation:
        # Base 1 + 1 for each decision point
        # This is a heuristic without the full AST — real implementation would walk the tree
        cc = 1  # base complexity

        # Add complexity for parameters (proxy for branching)
        cc += len(func.args)

        # Approximate from lines of code
        if func.lines_of_code > 100:
            cc += 5
        elif func.lines_of_code > 50:
            cc += 3
        elif func.lines_of_code > 20:
            cc += 1

        return ComplexityMetrics(
            cyclomatic_complexity=cc,
            lines_of_code=func.lines_of_code,
            parameter_count=len(func.args),
        )

    def _compute_maintainability(self, analysis: FileAnalysis) -> float:
        """Compute maintainability index for a file.

        Simplified formula: MI = 171 - 5.2 * ln(V) - 0.23 * G - 16.2 * ln(LOC)
        where V=halstead volume, G=cyclomatic complexity, LOC=lines of code.
        """
        loc = max(analysis.code_lines, 1)
        avg_cc = max(
            sum(
                1 + len(f.args)
                for f in analysis.functions
            ) / max(len(analysis.functions), 1),
            1,
        )

        mi = 171 - 5.2 * math.log(loc) - 0.23 * avg_cc - 16.2 * math.log(loc)
        return max(0, min(171, mi))

    def _detect_function_smells(
        self, name: str, metrics: ComplexityMetrics
    ) -> list[dict[str, Any]]:
        """Detect code smells in a function based on metrics."""
        smells = []

        if metrics.cyclomatic_complexity > self.complexity_threshold:
            smells.append({
                "type": "high_complexity",
                "name": name,
                "message": (
                    f"Function '{name}' has complexity {metrics.cyclomatic_complexity} "
                    f"(threshold: {self.complexity_threshold})"
                ),
                "severity": "error" if metrics.cyclomatic_complexity > 30 else "warning",
            })

        if metrics.lines_of_code > self.max_function_lines:
            smells.append({
                "type": "long_method",
                "name": name,
                "message": (
                    f"Function '{name}' has {metrics.lines_of_code} lines "
                    f"(threshold: {self.max_function_lines})"
                ),
                "severity": "warning",
            })

        if metrics.parameter_count > 5:
            smells.append({
                "type": "too_many_parameters",
                "name": name,
                "message": f"Function '{name}' has {metrics.parameter_count} parameters (threshold: 5)",
                "severity": "warning",
            })

        return smells
