# GlassCode

GlassCode is a terminal-first Python project explorer that turns a codebase into a browsable class diagram. It statically analyzes a project using Python's AST, resolves class relationships, and presents them in a TUI so you can inspect inheritance, composition, and dependency links without leaving the terminal.

## What it does

GlassCode scans a Python project directory, finds classes, and builds a lightweight model of:

- inheritance relationships (`extends`/base classes)
- composition links (attributes annotated or assigned with other types)
- dependency usage inside method signatures and annotations
- source locations for each class so you can jump directly to the implementation

The result is a navigable, filterable class browser suitable for mentally mapping a codebase before editing it.

## Features

- AST-based static analysis of Python source files
- recursive project scanning with common virtual-environment and build folders excluded
- class tree grouped by module
- class diagram view with inheritance and relation summaries
- linked-class panel for quick navigation between related classes
- source view showing the original code for the selected class
- keyboard-driven filtering and navigation in the terminal

## Installation

With uv:

```bash
uv sync
```

Or install the package in editable mode with pip:

```bash
python -m pip install -e .
```

## Usage

Run GlassCode against a Python project root:

```bash
glasscode /path/to/project
```

For example, from this repository:

```bash
uv run glasscode src
```

Or, if installed as a package:

```bash
glasscode /home/kristof/src/python/glasscode
```

## Keyboard shortcuts

- `q` — quit
- `Backspace` or `b` — go back to the previous class
- `/` — focus the class filter
- `t` — focus the class tree
- `l` — focus the linked classes panel
- `s` — focus the source view

## How it works

The project is split into two main pieces:

- `src/glasscode/analyzer.py` — parses Python files with `ast`, collects class metadata, resolves imports, and infers relations.
- `src/glasscode/app.py` — renders the Textual interface and lets you browse the diagram, related classes, and source code.

The analyzer intentionally focuses on a static, conservative view of the code: it gathers class definitions, resolves dotted names and imports, and infers likely object relationships from type annotations and assignments.

## Example workflow

1. Point GlassCode at a project root.
2. Filter the class list to the area you care about.
3. Select a class in the tree.
4. Inspect its parent classes, downstream subclasses, composed types, and the source definition.
5. Use the link panel to jump between connected classes and understand the object graph.

## Notes

This tool is designed for quick codebase orientation rather than full semantic verification. It is intentionally lightweight and terminal-first, making it useful for scanning architecture and understanding relationships in a Python project before deeper review.
