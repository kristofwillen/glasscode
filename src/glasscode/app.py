"""Textual TUI for browsing class diagrams of a Python project."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group, RenderableType
from rich.align import Align
from rich.columns import Columns
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from .analyzer import ClassInfo, Project, RelationKind, analyze


def _visibility(name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        return "-"
    if name.startswith("_") and not name.endswith("__"):
        return "#"
    return "+"


def _class_box(cls: ClassInfo, compact: bool = False) -> Panel:
    title = Text(cls.name, style="bold cyan")
    if compact:
        return Panel(title, expand=False, border_style="dim")

    stereotypes = []
    if cls.is_dataclass:
        stereotypes.append("dataclass")
    stereotypes += [d for d in cls.decorators if "dataclass" not in d]
    if any(b.split(".")[-1] in {"Enum", "IntEnum", "StrEnum", "Flag"} for b in cls.bases):
        stereotypes.append("enum")
    if any(b.split(".")[-1] in {"ABC", "Protocol"} for b in cls.bases):
        stereotypes.append("abstract")

    header = Text(justify="center")
    for s in stereotypes:
        header.append(f"«{s}»\n", style="italic magenta")
    header.append(cls.name, style="bold cyan")

    attrs = Text()
    for a in cls.attributes:
        attrs.append(f"{_visibility(a.name)} ", style="yellow")
        attrs.append(a.name, style="underline" if a.is_class_var and a.annotation is None else "")
        if a.annotation:
            attrs.append(f": {a.annotation}", style="green")
        attrs.append("\n")
    if not cls.attributes:
        attrs.append("(no attributes)", style="dim")
    attrs.rstrip()

    methods = Text()
    for m in cls.methods:
        methods.append(f"{_visibility(m.name)} ", style="yellow")
        tags = [d for d in m.decorators if d in {"staticmethod", "classmethod", "property", "abstractmethod"}]
        if tags:
            methods.append(f"«{', '.join(tags)}» ", style="italic magenta")
        if m.is_async:
            methods.append("async ", style="blue")
        methods.append(m.name, style="bold")
        methods.append(f"({m.args})", style="dim")
        if m.returns:
            methods.append(f" -> {m.returns}", style="green")
        methods.append("\n")
    if not cls.methods:
        methods.append("(no methods)", style="dim")
    methods.rstrip()

    return Panel(
        Group(header, Rule(style="dim"), attrs, Rule(style="dim"), methods),
        title=Text(cls.module, style="dim"),
        border_style="cyan",
        expand=False,
    )


class GlassCodeApp(App):
    TITLE = "GlassCode"
    CSS = """
    #sidebar { width: 34; border-right: solid $primary; }
    #filter { margin: 0 0 1 0; }
    #diagram-pane { width: 1fr; }
    #diagram-scroll { height: 2fr; border: round $primary; }
    #links { height: 1fr; border: round $secondary; }
    #source-scroll { width: 1fr; border: round $accent; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("backspace,b", "back", "Back"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("t", "focus('classes')", "Tree"),
        Binding("l", "focus('links')", "Links"),
        Binding("s", "focus('source-scroll')", "Source"),
    ]

    def __init__(self, project: Project):
        super().__init__()
        self.project = project
        self.current: str | None = None
        self.history: list[str] = []
        self._class_nodes: dict[str, TreeNode[str]] = {}
        self._link_targets: list[str | None] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Input(placeholder="Filter classes…", id="filter")
                yield Tree(self.project.root.name, id="classes")
            with Vertical(id="diagram-pane"):
                with VerticalScroll(id="diagram-scroll"):
                    yield Static(id="diagram")
                yield OptionList(id="links")
            with VerticalScroll(id="source-scroll"):
                yield Static(id="source")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = f"{self.project.root} — {len(self.project.classes)} classes"
        self.query_one("#diagram-scroll").border_title = "Class diagram"
        self.query_one("#links").border_title = "Linked classes (enter to go)"
        self.query_one("#source-scroll").border_title = "Source"
        self._build_tree("")
        if self.project.errors:
            self.notify(f"{len(self.project.errors)} file(s) failed to parse", severity="warning")
        if not self.project.classes:
            self.query_one("#diagram", Static).update("No classes found.")
        self.query_one("#classes").focus()

    def _build_tree(self, needle: str) -> None:
        tree = self.query_one("#classes", Tree)
        tree.clear()
        tree.root.expand()
        self._class_nodes.clear()
        needle = needle.lower()
        modules: dict[str, TreeNode[str]] = {}
        for qn, cls in sorted(self.project.classes.items(), key=lambda kv: (kv[1].module, kv[1].start_line)):
            if needle and needle not in qn.lower():
                continue
            if cls.module not in modules:
                modules[cls.module] = tree.root.add(Text(cls.module, style="dim"), expand=bool(needle))
            label = qn.removeprefix(cls.module + ".")
            self._class_nodes[qn] = modules[cls.module].add_leaf(label, data=qn)

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._build_tree(event.value)

    @on(Input.Submitted, "#filter")
    def _filter_submitted(self) -> None:
        self.query_one("#classes").focus()

    @on(Tree.NodeHighlighted, "#classes")
    @on(Tree.NodeSelected, "#classes")
    def _tree_node(self, event: Tree.NodeHighlighted | Tree.NodeSelected) -> None:
        # Ignore highlights caused by programmatic cursor moves from _sync_tree.
        if event.node.data and event.control.has_focus:
            self.show_class(event.node.data)

    @on(OptionList.OptionSelected, "#links")
    def _link_selected(self, event: OptionList.OptionSelected) -> None:
        target = self._link_targets[event.option_index]
        if target:
            self.show_class(target)

    def action_back(self) -> None:
        if self.history:
            self.show_class(self.history.pop(), record=False)

    def action_focus_filter(self) -> None:
        self.query_one("#filter").focus()

    def show_class(self, qualname: str, record: bool = True) -> None:
        if qualname == self.current or qualname not in self.project.classes:
            return
        if record and self.current:
            self.history.append(self.current)
        self.current = qualname
        cls = self.project.classes[qualname]
        self._sync_tree(qualname)
        self.query_one("#diagram", Static).update(self._render_diagram(cls))
        self.query_one("#diagram-scroll").scroll_home(animate=False)
        self._render_links(cls)
        self._render_source(cls)

    def _sync_tree(self, qualname: str) -> None:
        tree = self.query_one("#classes", Tree)
        node = self._class_nodes.get(qualname)
        if node is None:
            return
        if node.parent:
            node.parent.expand()
        if tree.cursor_node is not node:
            self.call_after_refresh(tree.move_cursor, node)

    def _render_diagram(self, cls: ClassInfo) -> RenderableType:
        classes = self.project.classes
        parts: list[RenderableType] = []

        parents: list[RenderableType] = []
        resolved = {r.target for r in cls.relations if r.kind is RelationKind.INHERITS}
        for qn in resolved:
            parents.append(_class_box(classes[qn], compact=True))
        resolved_names = {classes[qn].name for qn in resolved}
        for base in cls.bases:
            if base.split(".")[-1].split("[")[0] not in resolved_names:
                parents.append(Panel(Text(base, style="dim italic"), expand=False, border_style="dim"))
        if parents:
            parts.append(Align.center(Columns(parents)))
            parts.append(Align.center(Text("△\n│", style="bold")))

        parts.append(Align.center(_class_box(cls)))

        children = [c for c, r in self.project.incoming(cls.qualname) if r.kind is RelationKind.INHERITS]
        if children:
            parts.append(Align.center(Text("│\n┴", style="bold")))
            parts.append(Align.center(Columns([_class_box(c, compact=True) for c in children])))

        composed = [r for r in cls.relations if r.kind is RelationKind.COMPOSES]
        if composed:
            parts.append(Text())
            line = Text("◆── ", style="bold")
            line.append(", ".join(f"{classes[r.target].name} ({r.via})" for r in composed), style="cyan")
            parts.append(Align.center(line))
        return Group(*parts)

    def _render_links(self, cls: ClassInfo) -> None:
        links = self.query_one("#links", OptionList)
        links.clear_options()
        self._link_targets = []
        classes = self.project.classes
        incoming = self.project.incoming(cls.qualname)

        sections: list[tuple[str, list[tuple[str, str | None]]]] = [
            ("△ Inherits from", [(r.target, None) for r in cls.relations if r.kind is RelationKind.INHERITS]),
            ("▽ Subclasses", [(c.qualname, None) for c, r in incoming if r.kind is RelationKind.INHERITS]),
            ("◆ Has (attributes)", [(r.target, r.via) for r in cls.relations if r.kind is RelationKind.COMPOSES]),
            ("◇ Held by", [(c.qualname, r.via) for c, r in incoming if r.kind is RelationKind.COMPOSES]),
            ("┄▷ Uses (signatures)", [(r.target, r.via) for r in cls.relations if r.kind is RelationKind.DEPENDS]),
            ("◁┄ Used by", [(c.qualname, r.via) for c, r in incoming if r.kind is RelationKind.DEPENDS]),
        ]
        options: list[Option] = []
        for heading, items in sections:
            if not items:
                continue
            options.append(Option(Text(heading, style="bold"), disabled=True))
            self._link_targets.append(None)
            for target, via in items:
                label = Text("   ")
                label.append(classes[target].name, style="cyan")
                label.append(f"  {classes[target].module}", style="dim")
                if via:
                    label.append(f"  via {via}", style="italic")
                options.append(Option(label))
                self._link_targets.append(target)
        if not options:
            options.append(Option(Text("No linked classes", style="dim"), disabled=True))
            self._link_targets.append(None)
        links.add_options(options)

    def _render_source(self, cls: ClassInfo) -> None:
        lines = cls.path.read_text(encoding="utf-8", errors="replace").splitlines()
        code = "\n".join(lines[cls.start_line - 1 : cls.end_line])
        syntax = Syntax(
            code,
            "python",
            line_numbers=True,
            start_line=cls.start_line,
            theme="monokai",
            word_wrap=False,
        )
        self.query_one("#source", Static).update(syntax)
        scroll = self.query_one("#source-scroll")
        scroll.border_title = f"{cls.path.relative_to(self.project.root)}:{cls.start_line}-{cls.end_line}"
        scroll.scroll_home(animate=False)


def run(directory: Path) -> None:
    GlassCodeApp(analyze(directory)).run()
