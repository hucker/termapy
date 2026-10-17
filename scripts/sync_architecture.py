"""Sync ARCHITECTURE.md's module tree and test table from the docstrings.

Two blocks in ARCHITECTURE.md are generated, each between its markers:

    <!-- module-tree:start ... -->   one row per module of src/termapy
    <!-- test-table:start ... -->    every tests/test_*.py and what it covers

A row's description is the first paragraph of its module's docstring (a
package's comes from its ``__init__``), so the doc says what the code says
and a module cannot be missing from it.  The few entries that are not
Python modules have no docstring; their descriptions are ``NOTES`` below.
A description that is empty, or only identifiers, fails the sync: fix the
docstring, not the doc.

Line counts belong to release_prep (``ARCHITECTURE_LINE_COUNTS``): the tree
gives exactly those rows a ``(N lines)`` field and carries each number over
from the current file, so a feature branch never churns them.

Usage:
    uv run python scripts/sync_architecture.py          # writes if drifted
    uv run python scripts/sync_architecture.py --check  # exit 1 if drifted

``tests/test_architecture_sync.py`` runs ``--check``, so a hand edit to a
generated block, or a docstring change not synced, fails the suite.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "termapy"
TESTS = REPO_ROOT / "tests"
DOC = REPO_ROOT / "ARCHITECTURE.md"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_prep import ARCHITECTURE_LINE_COUNTS, count_lines  # noqa: E402 -- path set above

# Packages the tree opens, one row per module; any other package is one row.
EXPANDED = frozenset(
    {
        "builtins",
        "builtins/commands",
        "devices",
        "devices/converters",
        "dialogs",
        "mcp",
        "plugins",
        "profile",
        "protocol",
        "symbols",
        "symbols/converters",
        "usb",
        "widgets",
    }
)

# Entries that are not Python modules, so there is no docstring to read.
NOTES = {
    "builtins/commands/": "The command plugins, one module per command family (private _* helpers omitted)",
    "help/": "Markdown help pages, the source of the HTML help",
    "html/": "Generated HTML help, what the wheel ships (rebuilt at release)",
    "templates/": "Desktop-shortcut templates and icons for /cfg.icon (Windows, macOS, Linux)",
    "profile/schema.json": "The JSON Schema a device profile is validated against (Draft 2020-12)",
}

# A command plugin's or dialog's docstring opens by saying what kind of
# module it is; inside its own folder of the tree that is noise.
_KIND_PREFIX = re.compile(r"^(?:Built-in (?:plugin|transform \+ command)|Modal dialog): ")

_COUNT_ROW = re.compile(r"── ([\w.]+/?)\s+# \((\d+) lines\)")
_SOURCES = {
    "module-tree": "from the module docstrings",
    "test-table": "from the test module docstrings",
}


def _is_prose(text: str) -> bool:
    """True when a description says something in words, not only identifiers (``CfgConfirm``)."""
    return any(re.fullmatch(r"[a-z]{3,}", word) for word in re.findall(r"[A-Za-z_.]+", text))


def describe(path: Path, key: str) -> str:
    """The one-line description of a tree or table entry.

    Args:
        path: The module, package folder or file.
        key: Its path relative to src/termapy (or ``tests/<name>``), a folder ending in ``/``.

    Raises:
        ValueError: The entry has no description, or its docstring's first
            paragraph is only identifiers.
    """
    if key in NOTES:
        return NOTES[key]
    source = path / "__init__.py" if path.is_dir() else path
    doc = (
        ast.get_docstring(ast.parse(source.read_text(encoding="utf-8")))
        if source.is_file()
        else None
    )
    summary = " ".join((doc or "").strip().split("\n\n")[0].split()).removesuffix(".")
    summary = _KIND_PREFIX.sub("", summary)
    # reST inline markup (``x``, :mod:`x`) reads as one backtick pair in both blocks.
    summary = re.sub(r":\w+:`([^`]+)`", r"`\1`", summary).replace("``", "`")
    if not _is_prose(summary):
        raise ValueError(
            f"{key}: no description to show ({summary!r}); give its docstring a first "
            "paragraph that says what it is for, or add a NOTES entry if it is not Python"
        )
    return summary


def _children(folder: Path, rel: str) -> list[Path]:
    """What the tree shows inside a folder: subpackages, then modules, each alphabetical."""
    folders = sorted(
        path for path in folder.iterdir() if path.is_dir() and not path.name.startswith(("_", "."))
    )
    files = sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix in (".py", ".json")
        and not path.name.startswith("__")
        # The command plugins' private helpers stay out, as the tree says.
        and not (rel == "builtins/commands" and path.name.startswith("_"))
    )
    return folders + files


def render_tree(counts: dict[str, int]) -> str:
    """The module tree as a fenced text block.

    Args:
        counts: Line count per tracked top-level row (``app.py``, ``dialogs/``).
    """
    lines = ["src/termapy/"]

    def walk(folder: Path, rel: str, depth: int, lead: str) -> None:
        entries = _children(folder, rel)
        for index, path in enumerate(entries):
            last = index == len(entries) - 1
            name = path.name + ("/" if path.is_dir() else "")
            child_rel = f"{rel}/{path.name}" if rel else path.name
            description = describe(path, child_rel + ("/" if path.is_dir() else ""))
            branch = "└── " if last else "├── "
            if depth == 0:
                count = f"({counts[name]} lines)" if name in counts else ""
                lines.append(f"{branch}{name:<24}# {count:<13}{description}")
            else:
                width = max(24 - 4 * depth, len(name) + 1)
                lines.append(f"{lead}{branch}{name:<{width}}#   {'  ' * (depth - 1)}{description}")
            if path.is_dir() and child_rel in EXPANDED:
                walk(path, child_rel, depth + 1, lead + ("    " if last else "│   "))

    walk(SRC, "", 0, "")
    return "```text\n" + "\n".join(lines) + "\n```"


def render_test_table() -> str:
    """Every test module and the first paragraph of its docstring, as a markdown table."""
    rows = [("File", "Covers")] + [
        (path.name, describe(path, f"tests/{path.name}").replace("|", "\\|"))
        for path in sorted(TESTS.glob("test_*.py"))
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(2)]

    def line(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(cell.ljust(width) for cell, width in zip(cells, widths)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([line(rows[0]), separator, *(line(row) for row in rows[1:])])


def _block_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"<!-- {name}:start[^\n]*-->\n.*?<!-- {name}:end -->", re.DOTALL)


def tracked_counts(text: str) -> dict[str, int]:
    """Line count per tracked row: the number now in the doc, or counted if the row is new."""
    current = {name: int(number) for name, number in _COUNT_ROW.findall(text)}
    counts: dict[str, int] = {}
    for rel in ARCHITECTURE_LINE_COUNTS:
        name = Path(rel).name + ("/" if (REPO_ROOT / rel).is_dir() else "")
        counts[name] = current[name] if name in current else count_lines(rel)
    return counts


def rewrite(text: str) -> str:
    """``text`` with both generated blocks rebuilt.

    Raises:
        ValueError: A block's markers are missing, so nothing goes back to hand-kept silently.
    """
    bodies = {"module-tree": render_tree(tracked_counts(text)), "test-table": render_test_table()}
    for name, body in bodies.items():
        start = f"<!-- {name}:start (generated by scripts/sync_architecture.py {_SOURCES[name]} -- do not edit by hand) -->"
        block = f"{start}\n\n{body}\n\n<!-- {name}:end -->"
        text, found = _block_re(name).subn(lambda _match, block=block: block, text)
        if found != 1:
            raise ValueError(f"{name}:start / {name}:end markers not found in ARCHITECTURE.md")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a block has drifted")
    args = parser.parse_args(argv)
    current = DOC.read_text(encoding="utf-8")
    fresh = rewrite(current)
    if fresh == current:
        print("ARCHITECTURE.md is in sync")
        return 0
    if args.check:
        print(
            "ARCHITECTURE.md has drifted from the docstrings; run: uv run python scripts/sync_architecture.py"
        )
        return 1
    DOC.write_text(fresh, encoding="utf-8")
    print("ARCHITECTURE.md updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
