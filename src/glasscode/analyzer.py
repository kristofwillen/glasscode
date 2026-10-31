"""Static analysis of a Python project: extracts classes, members and relations."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    "site-packages",
    "build",
    "dist",
    "venv",
    "env",
}


class RelationKind(StrEnum):
    INHERITS = "inherits"
    COMPOSES = "composes"
    DEPENDS = "depends"


@dataclass
class Attribute:
    name: str
    annotation: str | None
    is_class_var: bool
    type_refs: list[ast.expr] = field(default_factory=list, repr=False)


@dataclass
class Method:
    name: str
    args: str
    returns: str | None
    decorators: list[str]
    is_async: bool
    type_refs: list[ast.expr] = field(default_factory=list, repr=False)


@dataclass
class Relation:
    kind: RelationKind
    target: str
    via: str | None = None


@dataclass
class ClassInfo:
    qualname: str
    name: str
    module: str
    path: Path
    start_line: int
    end_line: int
    bases: list[str]
    decorators: list[str]
    attributes: list[Attribute] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    base_refs: list[ast.expr] = field(default_factory=list, repr=False)

    @property
    def is_dataclass(self) -> bool:
        return any(d.split("(")[0].split(".")[-1] == "dataclass" for d in self.decorators)


@dataclass
class Project:
    root: Path
    classes: dict[str, ClassInfo]
    errors: list[str]

    def incoming(self, qualname: str) -> list[tuple[ClassInfo, Relation]]:
        return [
            (cls, rel)
            for cls in self.classes.values()
            for rel in cls.relations
            if rel.target == qualname
        ]


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or root.name


def _iter_python_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield Path(dirpath) / filename


def _annotation_refs(node: ast.expr | None) -> list[ast.expr]:
    """Collect Name/Attribute nodes in an annotation, including string annotations."""
    if node is None:
        return []
    refs: list[ast.expr] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            try:
                refs.extend(_annotation_refs(ast.parse(sub.value, mode="eval").body))
            except SyntaxError:
                pass
        elif isinstance(sub, ast.Name):
            refs.append(sub)
        elif isinstance(sub, ast.Attribute) and _dotted(sub):
            refs.append(sub)
    return refs


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _unparse(node: ast.expr | None) -> str | None:
    return ast.unparse(node) if node is not None else None


class _ModuleVisitor:
    def __init__(self, project_root: Path, path: Path, tree: ast.Module):
        self.path = path
        self.module = _module_name(project_root, path)
        self.is_package = path.name == "__init__.py"
        self.imports: dict[str, str] = {}
        self.classes: list[ClassInfo] = []
        self._collect_imports(tree)
        self._collect_classes(tree.body, prefix="")

    def _package(self, level: int) -> str:
        parts = self.module.split(".")
        drop = level - 1 if self.is_package else level
        return ".".join(parts[: len(parts) - drop] if drop else parts)

    def _collect_imports(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg = self._package(node.level)
                    base = f"{pkg}.{base}" if base else pkg
                for alias in node.names:
                    if alias.name != "*":
                        self.imports[alias.asname or alias.name] = f"{base}.{alias.name}"

    def _collect_classes(self, body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self.classes.append(self._class_info(node, prefix))
                self._collect_classes(node.body, prefix=f"{prefix}{node.name}.")

    def _class_info(self, node: ast.ClassDef, prefix: str) -> ClassInfo:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        info = ClassInfo(
            qualname=f"{self.module}.{prefix}{node.name}",
            name=node.name,
            module=self.module,
            path=self.path,
            start_line=start,
            end_line=node.end_lineno or node.lineno,
            bases=[ast.unparse(b) for b in node.bases],
            decorators=[ast.unparse(d) for d in node.decorator_list],
            base_refs=list(node.bases),
        )
        seen: dict[str, Attribute] = {}

        def add_attr(name: str, ann: ast.expr | None, class_var: bool, refs: list[ast.expr]) -> None:
            if name in seen:
                existing = seen[name]
                if existing.annotation is None and ann is not None:
                    existing.annotation = ast.unparse(ann)
                existing.type_refs.extend(refs)
                return
            attr = Attribute(name, _unparse(ann), class_var, refs)
            seen[name] = attr
            info.attributes.append(attr)

        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                add_attr(stmt.target.id, stmt.annotation, True, _annotation_refs(stmt.annotation))
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        add_attr(target.id, None, True, _value_refs(stmt.value))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                info.methods.append(_method_info(stmt))
                for name, ann, refs in _self_assignments(stmt):
                    add_attr(name, ann, False, refs)
        return info


def _value_refs(value: ast.expr | None) -> list[ast.expr]:
    """Infer a type from `Foo(...)` constructor calls."""
    if isinstance(value, ast.Call) and _dotted(value.func):
        return [value.func]
    return []


def _method_info(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> Method:
    refs: list[ast.expr] = []
    all_args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if fn.args.vararg:
        all_args.append(fn.args.vararg)
    if fn.args.kwarg:
        all_args.append(fn.args.kwarg)
    for arg in all_args:
        refs.extend(_annotation_refs(arg.annotation))
    refs.extend(_annotation_refs(fn.returns))
    return Method(
        name=fn.name,
        args=ast.unparse(fn.args),
        returns=_unparse(fn.returns),
        decorators=[ast.unparse(d) for d in fn.decorator_list],
        is_async=isinstance(fn, ast.AsyncFunctionDef),
        type_refs=refs,
    )


def _self_assignments(fn: ast.FunctionDef | ast.AsyncFunctionDef):
    if not fn.args.args:
        return
    self_name = fn.args.args[0].arg
    params = {a.arg: a.annotation for a in [*fn.args.args, *fn.args.kwonlyargs]}

    def is_self_attr(t: ast.expr) -> bool:
        return (
            isinstance(t, ast.Attribute)
            and isinstance(t.value, ast.Name)
            and t.value.id == self_name
        )

    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign) and is_self_attr(node.target):
            yield node.target.attr, node.annotation, _annotation_refs(node.annotation)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if not is_self_attr(target):
                    continue
                ann = None
                if isinstance(node.value, ast.Name) and params.get(node.value.id) is not None:
                    ann = params[node.value.id]
                refs = _annotation_refs(ann) if ann is not None else _value_refs(node.value)
                yield target.attr, ann, refs


class _Resolver:
    def __init__(self, classes: dict[str, ClassInfo], root: Path):
        self.classes = classes
        self.top_levels = {root.name} | {qn.split(".")[0] for qn in classes}
        self.by_name: dict[str, list[str]] = {}
        for qn, cls in classes.items():
            self.by_name.setdefault(cls.name, []).append(qn)

    def _by_suffix(self, dotted: str) -> str | None:
        if dotted in self.classes:
            return dotted
        matches = [qn for qn in self.classes if qn.endswith("." + dotted)]
        return matches[0] if len(matches) == 1 else None

    def resolve(self, ref: ast.expr, visitor: _ModuleVisitor, owner: ClassInfo) -> str | None:
        dotted = _dotted(ref)
        if not dotted:
            return None
        head, _, rest = dotted.partition(".")
        # Nested class siblings / same module
        owner_scope = owner.qualname.rsplit(".", 1)[0]
        for scope in (owner.qualname, owner_scope, visitor.module):
            candidate = f"{scope}.{dotted}"
            if candidate in self.classes:
                return candidate
        if head in visitor.imports:
            full = visitor.imports[head] + (f".{rest}" if rest else "")
            if found := self._by_suffix(full):
                return found
            if full.split(".")[0] not in self.top_levels:
                return None  # imported from a third-party module
        if not rest:
            matches = self.by_name.get(head, [])
            if len(matches) == 1:
                return matches[0]
        return self._by_suffix(dotted) if rest else None


def analyze(root: Path) -> Project:
    root = root.resolve()
    classes: dict[str, ClassInfo] = {}
    owners: list[tuple[_ModuleVisitor, ClassInfo]] = []
    errors: list[str] = []

    for path in _iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        except (SyntaxError, ValueError) as exc:
            errors.append(f"{path.relative_to(root)}: {exc}")
            continue
        visitor = _ModuleVisitor(root, path, tree)
        for cls in visitor.classes:
            classes[cls.qualname] = cls
            owners.append((visitor, cls))

    resolver = _Resolver(classes, root)
    for visitor, cls in owners:
        seen: set[tuple[RelationKind, str]] = set()

        def link(kind: RelationKind, ref: ast.expr, via: str | None) -> None:
            target = resolver.resolve(ref, visitor, cls)
            if target and target != cls.qualname and (kind, target) not in seen:
                seen.add((kind, target))
                cls.relations.append(Relation(kind, target, via))

        for ref in cls.base_refs:
            link(RelationKind.INHERITS, ref, None)
        for attr in cls.attributes:
            for ref in attr.type_refs:
                link(RelationKind.COMPOSES, ref, attr.name)
        for method in cls.methods:
            for ref in method.type_refs:
                link(RelationKind.DEPENDS, ref, f"{method.name}()")

    return Project(root=root, classes=classes, errors=errors)
