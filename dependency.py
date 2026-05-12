"""Dependency graph builder for codebase analysis."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeforge.analyzers.ast_parser import ASTParser, FileAnalysis


@dataclass
class DependencyNode:
    """A node in the dependency graph."""

    file_path: str
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    internal_deps: list[str] = field(default_factory=list)  # deps within same project
    external_deps: list[str] = field(default_factory=list)  # third-party deps

    @property
    def fan_in(self) -> int:
        """Number of modules that depend on this module."""
        return len(self.imported_by)

    @property
    def fan_out(self) -> int:
        """Number of modules this module depends on."""
        return len(self.imports)

    @property
    def instability(self) -> float:
        """Instability metric: fan_out / (fan_in + fan_out)."""
        total = self.fan_in + self.fan_out
        return self.fan_out / total if total > 0 else 0.0


@dataclass
class DependencyGraph:
    """Complete dependency graph for a codebase."""

    nodes: dict[str, DependencyNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    external_packages: set[str] = field(default_factory=set)
    root_path: str = ""

    @property
    def total_files(self) -> int:
        return len(self.nodes)

    @property
    def total_edges(self) -> int:
        return len(self.edges)

    def get_most_depended_on(self, top_n: int = 10) -> list[tuple[str, int]]:
        """Get files with the most dependents (highest fan-in)."""
        ranked = sorted(
            self.nodes.items(),
            key=lambda x: x[1].fan_in,
            reverse=True,
        )
        return [(path, node.fan_in) for path, node in ranked[:top_n]]

    def get_most_dependent(self, top_n: int = 10) -> list[tuple[str, int]]:
        """Get files with the most dependencies (highest fan-out)."""
        ranked = sorted(
            self.nodes.items(),
            key=lambda x: x[1].fan_out,
            reverse=True,
        )
        return [(path, node.fan_out) for path, node in ranked[:top_n]]

    def to_mermaid(self) -> str:
        """Export dependency graph as Mermaid diagram syntax."""
        lines = ["graph TD"]
        for src, dst in self.edges[:50]:  # Limit for readability
            src_short = Path(src).stem
            dst_short = Path(dst).stem
            lines.append(f"    {src_short} --> {dst_short}")
        return "\n".join(lines)


class DependencyGraphBuilder:
    """Build a dependency graph by analyzing import statements across files."""

    def __init__(self, root_path: str | Path) -> None:
        self.root_path = Path(root_path)
        self.ast_parser = ASTParser()
        self._project_packages: set[str] = set()

    def build(self, include_external: bool = True) -> DependencyGraph:
        """Build the dependency graph for the project."""
        graph = DependencyGraph(root_path=str(self.root_path))

        # Discover project package names
        self._discover_packages()

        # Parse all Python files
        file_analyses: dict[str, FileAnalysis] = {}
        for py_file in self.root_path.rglob("*.py"):
            if self._should_skip(py_file):
                continue
            try:
                analysis = self.ast_parser.parse_file(py_file)
                rel_path = str(py_file.relative_to(self.root_path))
                file_analyses[rel_path] = analysis
            except (SyntaxError, UnicodeDecodeError):
                continue

        # Build nodes
        for rel_path, analysis in file_analyses.items():
            node = DependencyNode(file_path=rel_path)

            for imp in analysis.imports:
                module = imp.module if imp.is_from_import else imp.names[0]
                resolved = self._resolve_import(module, rel_path)

                if resolved:
                    node.internal_deps.append(resolved)
                    node.imports.append(resolved)
                    graph.edges.append((rel_path, resolved))
                elif include_external:
                    top_pkg = module.split(".")[0]
                    if top_pkg and not top_pkg.startswith("."):
                        node.external_deps.append(top_pkg)
                        graph.external_packages.add(top_pkg)

            graph.nodes[rel_path] = node

        # Build reverse edges (imported_by)
        for src, dst in graph.edges:
            if dst in graph.nodes:
                graph.nodes[dst].imported_by.append(src)

        # Detect cycles
        graph.cycles = self._detect_cycles(graph)

        return graph

    def _discover_packages(self) -> None:
        """Discover project package names from directory structure."""
        for item in self.root_path.iterdir():
            if item.is_dir() and (item / "__init__.py").exists():
                self._project_packages.add(item.name)

    def _resolve_import(self, module: str, from_file: str) -> str | None:
        """Try to resolve an import to a file path within the project."""
        if not module or module.startswith("."):
            # Relative import
            parts = module.lstrip(".").split(".")
            if parts == [""]:
                parts = []
            from_dir = str(Path(from_file).parent)
            candidate = Path(self.root_path) / from_dir / "/".join(parts)
            if candidate.with_suffix(".py").exists():
                return str(candidate.with_suffix(".py").relative_to(self.root_path))
            if (candidate / "__init__.py").exists():
                return str((candidate / "__init__.py").relative_to(self.root_path))
            return None

        # Absolute import — check if it's a project package
        top_pkg = module.split(".")[0]
        if top_pkg in self._project_packages:
            parts = module.split(".")
            candidate = self.root_path / "/".join(parts)
            if candidate.with_suffix(".py").exists():
                return str(candidate.with_suffix(".py").relative_to(self.root_path))
            if (candidate / "__init__.py").exists():
                return str((candidate / "__init__.py").relative_to(self.root_path))

        return None

    def _should_skip(self, path: Path) -> bool:
        """Check if a file should be skipped."""
        skip_dirs = {"__pycache__", ".git", "node_modules", "venv", ".venv", "vendor"}
        return any(part in skip_dirs for part in path.parts)

    def _detect_cycles(self, graph: DependencyGraph) -> list[list[str]]:
        """Detect cycles in the dependency graph using DFS."""
        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for src, dst in graph.edges:
                if src == node:
                    if dst not in visited:
                        dfs(dst)
                    elif dst in rec_stack:
                        # Found a cycle
                        cycle_start = path.index(dst)
                        cycles.append(path[cycle_start:] + [dst])

            path.pop()
            rec_stack.discard(node)

        for node in graph.nodes:
            if node not in visited:
                dfs(node)

        return cycles
