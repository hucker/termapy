"""The id sources a test's ``rule`` marker may name, the marker index, and the checks that keep them honest.

A test that pins a rule says so with ``@pytest.mark.rule("<id>")``: one marker
per rule, ``pytestmark`` for a whole module.  The id is a row of the rule
registry (``docs/rules.md``) or a section of a spec that labels its sections
with ids (``docs/param-spec-implementation.md`` today); a requirements file
added later is one more row of :data:`SOURCES`.  ``conftest.py`` refuses a
marker that names nothing at collection and runs the tests of one id under
``--rule``; ``test_architecture.py`` checks the reverse, that every test a
registry row names carries the row's marker; ``scripts/sync_traceability.py``
renders the index into ``docs/traceability-matrix.md``.  ``docs/traceability.md`` is
the process and the plan.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TESTS = REPO / "tests"

MARKER = "rule"
RULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
# A registry row names a test function, a Test class or a test file.
TEST_REF = re.compile(r"\btest_\w+(?:\.py)?|\bTest[A-Z]\w*")
PROBE_REF = re.compile(r"probe `([\w.]+)`")


# ── Id sources ────────────────────────────────────────────────────────────────


def registry_rows(text: str) -> list[dict[str, str]]:
    """The registry table's data rows, each a dict keyed by the header cells."""
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the separator row
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


def registry_ids(text: str) -> set[str]:
    """The ids of a rule registry's rows."""
    return {row.get("id", "") for row in registry_rows(text)}


def spec_ids(text: str, prefix: str) -> set[str]:
    """The ids of a spec that heads a section ``### <id>`` or cites one as ``**<id>**``.

    Every id starts with ``prefix``, the spec's own namespace, so a bold phrase
    that happens to be kebab-case is not an id.
    """
    pattern = re.compile(rf"(?m)(?:^### |\*\*)({re.escape(prefix)}[a-z0-9-]+)")
    return set(pattern.findall(text))


@dataclass(frozen=True)
class IdSource:
    """A document whose ids a ``rule`` marker may name.

    ``title`` heads the document's section of the traceability matrix;
    ``what`` says what one of its ids is, in a refusal.
    """

    path: Path
    title: str
    what: str
    parse: Callable[[str], set[str]]

    def ids(self) -> set[str]:
        """Every id the document holds today."""
        return self.parse(self.path.read_text(encoding="utf-8"))


SOURCES: tuple[IdSource, ...] = (
    IdSource(DOCS / "rules.md", "Registry", "a rule registry row", registry_ids),
    IdSource(
        DOCS / "param-spec-implementation.md",
        "Spec",
        "a param spec section",
        lambda text: spec_ids(text, "param-"),
    ),
)


def known_ids() -> dict[str, str]:
    """Every id a marker may name, each with what it is (its source's ``what``)."""
    found: dict[str, str] = {}
    for source in SOURCES:
        for rule_id in source.ids():
            found.setdefault(rule_id, source.what)
    return found


def marker_problems(
    marked: Iterable[tuple[str, tuple[object, ...]]],
    ids: Collection[str],
) -> list[str]:
    """One line per ``rule`` marker that names nothing.

    ``marked`` pairs a test's name with one marker's arguments; a marker takes
    exactly one argument, a known id.
    """
    problems: list[str] = []
    for name, args in marked:
        if len(args) != 1 or not isinstance(args[0], str):
            problems.append(f"{name}: rule() takes one id, got {args!r}")
        elif args[0] not in ids:
            problems.append(f"{name}: unknown rule id {args[0]!r}")
    return problems


# ── The marker index ──────────────────────────────────────────────────────────
#
# Read from the source with ``ast``, never by importing a test module, so the
# guard and the matrix see every test file the same way pytest's collection
# does and need no fixtures to do it.


@dataclass
class NodeMarks:
    """One test node's rule ids: its own decorators' and the ones it inherits from above.

    ``functions`` counts the test functions a class holds, nested classes
    included; it is 0 for a function.
    """

    own: set[str] = field(default_factory=set)
    inherited: set[str] = field(default_factory=set)
    functions: int = 0

    @property
    def ids(self) -> set[str]:
        """Every id in effect on the node."""
        return self.own | self.inherited


@dataclass
class ModuleMarks:
    """One test module's markers: the module's own ids, each Test class's and each test function's."""

    file: str
    module_ids: set[str] = field(default_factory=set)
    classes: dict[str, NodeMarks] = field(default_factory=dict)
    functions: dict[str, NodeMarks] = field(default_factory=dict)

    @property
    def test_functions(self) -> int:
        """How many test functions the module holds."""
        return len(self.functions)


@dataclass
class MarkIndex:
    """Every test under tests/ with the rule ids in effect on it, by function, class and file name.

    A name shared by tests in two files maps to the union of their ids, so a
    row that names it is satisfied when any of them carries the marker.
    ``markers`` is every ``rule`` marker literal with where it stands and its
    arguments, for the check that each names a known id; ``problems`` is a
    shape that reads as marked but is not: a second ``pytestmark`` assignment,
    which replaces the first at import.  ``modules`` keeps each file's marks
    whole, for the matrix.
    """

    functions: dict[str, set[str]] = field(default_factory=dict)
    classes: dict[str, set[str]] = field(default_factory=dict)
    files: dict[str, set[str]] = field(default_factory=dict)
    markers: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    modules: list[ModuleMarks] = field(default_factory=list)


def rule_calls(expr: ast.expr) -> list[tuple[object, ...]]:
    """The argument tuples of every ``pytest.mark.rule(...)`` in ``expr``, a call or a list of them."""
    found: list[tuple[object, ...]] = []
    for call in expr.elts if isinstance(expr, ast.List) else [expr]:
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == MARKER
            and isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "mark"
        ):
            found.append(
                tuple(arg.value if isinstance(arg, ast.Constant) else "<expr>" for arg in call.args)
            )
    return found


def ids_named(calls: list[tuple[object, ...]]) -> set[str]:
    """The ids well-formed ``rule`` calls name; a malformed one names nothing and is reported elsewhere."""
    return {args[0] for args in calls if len(args) == 1 and isinstance(args[0], str)}


def _count_test_functions(body: list[ast.stmt]) -> int:
    count = 0
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            count += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            count += _count_test_functions(node.body)
    return count


def index_module(tree: ast.Module, rel: str, index: MarkIndex) -> ModuleMarks:
    """Add one test module's tests to ``index``; returns the module's marks."""
    marks = ModuleMarks(rel)
    assignments = 0
    for node in tree.body:
        is_pytestmark = isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        )
        if is_pytestmark:
            assert isinstance(node, ast.Assign)
            assignments += 1
            calls = rule_calls(node.value)
            index.markers.extend((rel, args) for args in calls)
            marks.module_ids.update(ids_named(calls))
    if assignments > 1:
        index.problems.append(
            f"{rel}: {assignments} pytestmark assignments; only the last applies, so merge "
            "them into one list"
        )
    index.files[rel] = marks.module_ids

    def walk(body: list[ast.stmt], inherited: set[str], prefix: str) -> None:
        for node in body:
            is_test = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("test")
            )
            is_class = isinstance(node, ast.ClassDef) and node.name.startswith("Test")
            if not (is_test or is_class):
                continue
            assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            calls = [args for decorator in node.decorator_list for args in rule_calls(decorator)]
            qualname = f"{prefix}{node.name}"
            index.markers.extend((f"{rel}::{qualname}", args) for args in calls)
            own = ids_named(calls)
            if isinstance(node, ast.ClassDef):
                marks.classes[qualname] = NodeMarks(
                    own, set(inherited), _count_test_functions(node.body)
                )
                index.classes.setdefault(node.name, set()).update(inherited | own)
                walk(node.body, inherited | own, f"{qualname}.")
            else:
                marks.functions[qualname] = NodeMarks(own, set(inherited))
                index.functions.setdefault(node.name, set()).update(inherited | own)

    walk(tree.body, set(marks.module_ids), "")
    index.modules.append(marks)
    return marks


def index_tests(tests_dir: Path = TESTS) -> MarkIndex:
    """Index every test module under ``tests_dir``."""
    index = MarkIndex()
    for path in sorted(tests_dir.rglob("test_*.py")):
        index_module(ast.parse(path.read_text(encoding="utf-8")), path.name, index)
    return index


# ── The matrix's view of the index ────────────────────────────────────────────


@dataclass
class FileEntry:
    """What one test file contributes to one id, without repeating what a node inherits.

    ``module`` says the whole module carries the id (``module_functions`` tests);
    ``classes`` are the Test classes that carry it themselves, each with its
    test count; ``functions`` the tests that carry it themselves.
    """

    module: bool = False
    module_functions: int = 0
    classes: list[tuple[str, int]] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


def matrix_entries(index: MarkIndex) -> dict[str, dict[str, FileEntry]]:
    """``{id: {file: entry}}`` for every id a marker under tests/ names."""
    entries: dict[str, dict[str, FileEntry]] = {}

    def entry(rule_id: str, file: str) -> FileEntry:
        return entries.setdefault(rule_id, {}).setdefault(file, FileEntry())

    for module in index.modules:
        for rule_id in module.module_ids:
            found = entry(rule_id, module.file)
            found.module = True
            found.module_functions = module.test_functions
        for qualname, marks in module.classes.items():
            for rule_id in marks.own - marks.inherited:
                entry(rule_id, module.file).classes.append((qualname, marks.functions))
        for qualname, marks in module.functions.items():
            for rule_id in marks.own - marks.inherited:
                entry(rule_id, module.file).functions.append(qualname)
    return entries
