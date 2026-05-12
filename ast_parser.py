"""AST-based code parsing and structural analysis."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FunctionInfo:
    """Metadata extracted from a function/method definition."""

    name: str
    lineno: int
    end_lineno: int
    args: list[str]
    decorators: list[str]
    docstring: str | None
    complexity: int = 1
    lines_of_code: int = 0
    is_method: bool = False
    parent_class: str | None = None

    @property
    def signature(self) -> str:
        args_str = ", ".join(self.args)
        return f"{self.name}({args_str})"


@dataclass
class ClassInfo:
    """Metadata extracted from a class definition."""

    name: str
    lineno: int
    end_lineno: int
    bases: list[str]
    methods: list[FunctionInfo] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    docstring: str | None = None
    lines_of_code: int = 0

    @property
    def method_count(self) -> int:
        return len(self.methods)


@dataclass
class ImportInfo:
    """Metadata for an import statement."""

    module: str
    names: list[str]
    lineno: int
    is_from_import: bool = False


@dataclass
class FileAnalysis:
    """Complete AST analysis result for a single file."""

    path: str
    language: str = "python"
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    global_variables: list[str] = field(default_factory=list)
    total_lines: int = 0
    code_lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0

    @property
    def total_functions(self) -> int:
        return len(self.functions) + sum(c.method_count for c in self.classes)

    @property
    def total_classes(self) -> int:
        return len(self.classes)


class ASTParser:
    """Python AST parser for extracting code structure and metadata."""

    def parse_file(self, file_path: str | Path) -> FileAnalysis:
        """Parse a Python file and extract structural information."""
        file_path = Path(file_path)
        source = file_path.read_text(encoding="utf-8")

        return self.parse_source(source, str(file_path))

    def parse_source(self, source: str, file_path: str = "<string>") -> FileAnalysis:
        """Parse Python source code and extract structural information."""
        analysis = FileAnalysis(path=file_path)

        # Count line types
        lines = source.splitlines()
        analysis.total_lines = len(lines)
        for line in lines:
            stripped = line.strip()
            if not stripped:
                analysis.blank_lines += 1
            elif stripped.startswith("#"):
                analysis.comment_lines += 1
            else:
                analysis.code_lines += 1

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return analysis

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                analysis.functions.append(self._extract_function(node))
            elif isinstance(node, ast.ClassDef):
                analysis.classes.append(self._extract_class(node))
            elif isinstance(node, ast.Import | ast.ImportFrom):
                analysis.imports.extend(self._extract_imports(node))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        analysis.global_variables.append(target.id)

        return analysis

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_class: str | None = None,
        is_method: bool = False,
    ) -> FunctionInfo:
        """Extract function metadata from an AST node."""
        args = []
        for arg in node.args.args:
            if arg.arg != "self" and arg.arg != "cls":
                args.append(arg.arg)

        decorators = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(ast.dump(dec))

        docstring = ast.get_docstring(node)
        end_lineno = node.end_lineno or node.lineno
        lines_of_code = end_lineno - node.lineno

        return FunctionInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=end_lineno,
            args=args,
            decorators=decorators,
            docstring=docstring,
            lines_of_code=lines_of_code,
            is_method=is_method,
            parent_class=parent_class,
        )

    def _extract_class(self, node: ast.ClassDef) -> ClassInfo:
        """Extract class metadata from an AST node."""
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(f"{ast.dump(base)}")

        decorators = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)

        docstring = ast.get_docstring(node)
        end_lineno = node.end_lineno or node.lineno

        methods = []
        for item in node.body:
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                methods.append(self._extract_function(item, parent_class=node.name, is_method=True))

        return ClassInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=end_lineno,
            bases=bases,
            methods=methods,
            decorators=decorators,
            docstring=docstring,
            lines_of_code=end_lineno - node.lineno,
        )

    def _extract_imports(self, node: ast.Import | ast.ImportFrom) -> list[ImportInfo]:
        """Extract import metadata from an AST node."""
        imports = []

        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(ImportInfo(
                    module=alias.name,
                    names=[alias.asname or alias.name],
                    lineno=node.lineno,
                    is_from_import=False,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            imports.append(ImportInfo(
                module=module,
                names=names,
                lineno=node.lineno,
                is_from_import=True,
            ))

        return imports
