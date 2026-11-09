"""The rule marker's checks and the matrix: an unknown id is refused at collection, ``--rule`` selects by id, the index reads every marker shape, and the generated matrix stays current.

The id sources, their parsers and the marker index are pinned directly; the
two session-level behaviors are driven as real child pytest runs over a
scratch test file, with the suite's own conftest loaded as a plugin, so they
are seen the way a developer meets them.  The process is
``docs/traceability.md``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from traceability import (
    SOURCES,
    MarkIndex,
    index_module,
    known_ids,
    marker_problems,
    matrix_entries,
    registry_ids,
    spec_ids,
)

TESTS = Path(__file__).parent
REPO = TESTS.parent
_SCRIPTS_DIR = REPO / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from sync_traceability import MATRIX, main, render_matrix  # noqa: E402 -- path set above

pytestmark = pytest.mark.rule("rules-registry")


class TestIdSources:
    """Each source's parser finds its ids and nothing else."""

    def test_registry_ids_are_the_table_rows(self):
        """A registry id is the first cell of a data row; the header and separator are not rows."""
        table = (
            "| id | statement | kind |\n"
            "|---|---|---|\n"
            "| `one-rule` | says a thing | guarantee |\n"
            "| `two-rule` | says another | assumption |\n"
        )
        assert registry_ids(table) == {"one-rule", "two-rule"}

    def test_spec_ids_are_headings_and_bold_citations_in_the_prefix(self):
        """A spec id is a ``###`` heading or a ``**bold**`` citation starting with the spec's prefix."""
        text = (
            "### param-decl-dataclass\n"
            "body (spec: **param-decl-validation**)\n"
            "**long-term** plans and `param-not-bold` in code\n"
            "## param-not-a-section\n"
        )
        assert spec_ids(text, "param-") == {"param-decl-dataclass", "param-decl-validation"}, (
            "a bold kebab phrase outside the prefix and a level-2 heading are not ids"
        )

    def test_every_source_exists_and_holds_ids(self):
        """Each id source in the table is a real document with at least one id."""
        for source in SOURCES:
            assert source.path.is_file(), f"{source.path} is an id source that is not there"
            assert source.ids(), f"{source.path} holds no ids"

    def test_known_ids_span_the_registry_and_the_spec(self):
        """The one id set a marker may name holds a registry row and a spec section alike."""
        ids = known_ids()
        assert ids["rules-registry"] == "a rule registry row"
        assert ids["param-decl-validation"] == "a param spec section"


class TestMarkerProblems:
    """The one check behind collection's refusal: one id per marker, and a known one."""

    def test_a_known_id_is_no_problem(self):
        """A marker naming a known id passes."""
        assert marker_problems([("t.py::test_a", ("good-id",))], {"good-id"}) == []

    def test_an_unknown_id_and_a_bad_arity_are_each_one_line(self):
        """An unknown id and a marker with no or two arguments are each reported once, by test."""
        problems = marker_problems(
            [
                ("t.py::test_a", ("no-such",)),
                ("t.py::test_b", ()),
                ("t.py::test_c", ("good-id", "other")),
            ],
            {"good-id"},
        )
        assert problems == [
            "t.py::test_a: unknown rule id 'no-such'",
            "t.py::test_b: rule() takes one id, got ()",
            "t.py::test_c: rule() takes one id, got ('good-id', 'other')",
        ]


_PROBE_MODULE = (
    "import pytest\n"
    'pytestmark = [pytest.mark.slow, pytest.mark.rule("mod-rule")]\n'
    '@pytest.mark.rule("cls-rule")\n'
    "class TestThing:\n"
    '    @pytest.mark.rule("fn-rule")\n'
    "    def test_method(self):\n"
    "        pass\n"
    "    def test_plain(self):\n"
    "        pass\n"
    "@pytest.mark.rule()\n"
    "def test_bare():\n"
    "    pass\n"
    "def test_outside():\n"
    "    pass\n"
)


class TestMarkerIndex:
    """The ast index behind the guard and the matrix reads every marker shape as pytest applies it."""

    def test_marker_index_sees_every_shape_on_a_probe(self):
        """A module pytestmark, a class marker and a function marker are read, inherited downward, and each literal is recorded."""
        # Arrange
        index = MarkIndex()

        # Act
        marks = index_module(ast.parse(_PROBE_MODULE), "probe.py", index)

        # Assert
        assert index.files == {"probe.py": {"mod-rule"}}
        assert index.classes == {"TestThing": {"mod-rule", "cls-rule"}}
        assert index.functions == {
            "test_method": {"mod-rule", "cls-rule", "fn-rule"},
            "test_plain": {"mod-rule", "cls-rule"},
            "test_bare": {"mod-rule"},
            "test_outside": {"mod-rule"},
        }, "a marker is inherited from the module and the class; a bare rule() names nothing"
        assert index.markers == [
            ("probe.py", ("mod-rule",)),
            ("probe.py::TestThing", ("cls-rule",)),
            ("probe.py::TestThing.test_method", ("fn-rule",)),
            ("probe.py::test_bare", ()),
        ], "every rule() literal is recorded where it stands, the malformed one included"
        assert marks.classes["TestThing"].functions == 2, "a class knows how many tests it holds"
        assert marks.test_functions == 4

    def test_second_pytestmark_guard_fires_on_a_probe(self):
        """A module with two pytestmark assignments is flagged: the second replaces the first at import."""
        # Arrange
        probe = (
            "import pytest\n"
            'pytestmark = pytest.mark.rule("first-rule")\n'
            'pytestmark = pytest.mark.rule("second-rule")\n'
            "def test_thing():\n"
            "    pass\n"
        )
        index = MarkIndex()

        # Act
        index_module(ast.parse(probe), "probe.py", index)

        # Assert
        assert index.problems == [
            "probe.py: 2 pytestmark assignments; only the last applies, so merge them into one list"
        ]

    def test_matrix_entries_list_each_node_once_without_what_it_inherits(self):
        """An id reaches the matrix from the node that carries it: the module, a class or a function, never twice."""
        # Arrange
        index = MarkIndex()
        index_module(ast.parse(_PROBE_MODULE), "probe.py", index)

        # Act
        entries = matrix_entries(index)

        # Assert
        assert entries["mod-rule"]["probe.py"].module is True
        assert entries["mod-rule"]["probe.py"].module_functions == 4
        assert entries["mod-rule"]["probe.py"].classes == [], (
            "the class inherits mod-rule, so it is not listed again"
        )
        assert entries["cls-rule"]["probe.py"].classes == [("TestThing", 2)]
        assert entries["fn-rule"]["probe.py"].functions == ["TestThing.test_method"]


@pytest.mark.rule("traceability-matrix-generated")
class TestMatrix:
    """The generated matrix matches the markers, and its rendering says what each test is to a rule."""

    def test_the_matrix_is_in_sync(self):
        """docs/traceability-matrix.md matches the markers under tests/ and the registry's rows."""
        assert main(["--check"]) == 0, (
            f"{MATRIX.name} was hand-edited or a marker changed; "
            "run: uv run python scripts/sync_traceability.py"
        )

    def test_matrix_render_on_a_probe(self):
        """A section names each test by file, marks the row's guard and probe, counts a module and a class, and says when nothing pins an id."""
        # Arrange
        index = MarkIndex()
        index_module(ast.parse(_PROBE_MODULE), "probe.py", index)
        rows = [
            {
                "id": "fn-rule",
                "kind": "guarantee",
                "category": "structure",
                "enforced by": "`test_method`; probe `test_bare`",
            },
            {"id": "mod-rule", "kind": "assumption", "category": "data", "enforced by": "review"},
            {
                "id": "lonely-rule",
                "kind": "assumption",
                "category": "data",
                "enforced by": "review",
            },
        ]

        # Act
        text = render_matrix(rows, [("Spec", "spec.md", {"cls-rule", "spec-only"})], index)

        # Assert
        assert (
            "Ids: 5: 3 registry rows (guarantees 1, assumptions 2), 2 spec sections in `spec.md`. "
            "Pinned by at least one test: 3. Pinned by none: 2."
        ) in text
        assert (
            "## Pinned by no test\n\n| id | source | kind |\n|---|---|---|\n"
            "| `lonely-rule` | `rules.md` | assumption, data |\n"
            "| `spec-only` | `spec.md` | spec section |\n"
        ) in text, "the unpinned ids are one table up front, the registry's first"
        assert (
            "### `fn-rule` (guarantee, structure)\n\n- `probe.py`: `TestThing.test_method` **guard**\n"
        ) in text, "the function the row names is the guard"
        assert (
            "### `mod-rule` (assumption, data)\n\n- `probe.py`: the module (4 functions)\n" in text
        )
        assert "### `lonely-rule` (assumption, data)\n\n- no tagged test\n" in text
        assert (
            "## Spec (`spec.md`)\n\n### `cls-rule` (spec section)\n\n- `probe.py`: `TestThing` (2 functions)\n"
            in text
        )
        assert "### `spec-only` (spec section)\n\n- no tagged test\n" in text


def _child_pytest(scratch: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Collect ``scratch`` in a child pytest with the suite's conftest and pyproject in force."""
    env = {**os.environ, "PYTHONPATH": str(TESTS), "COLUMNS": "200"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(scratch),
            "-c",
            str(REPO / "pyproject.toml"),
            "--rootdir",
            str(REPO),
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-n",
            "0",
            "--no-cov",
            "-q",
            "--co",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
        timeout=180,
        check=False,
    )


@pytest.mark.slow
class TestSession:
    """What a developer sees: the refusal at collection and the ``--rule`` selection."""

    def test_a_marker_naming_nothing_stops_the_session(self, tmp_path):
        """A ``rule`` marker with an id no source holds ends the run at collection, naming the test and the id."""
        # Arrange
        scratch = tmp_path / "test_probe.py"
        scratch.write_text(
            "import pytest\n\n\n"
            '@pytest.mark.rule("no-such-rule")\n'
            "def test_tagged():\n"
            '    """A probe with a marker that names nothing."""\n',
            encoding="utf-8",
        )

        # Act
        result = _child_pytest(scratch)

        # Assert
        assert result.returncode == 4, (
            f"pytest's usage-error exit:\n{result.stdout}\n{result.stderr}"
        )
        assert "unknown rule id 'no-such-rule'" in result.stderr, "the refusal names the id"
        assert "test_tagged" in result.stderr, "and the test that carries it"

    def test_rule_selects_the_tests_that_name_it(self, tmp_path):
        """``--rule ID`` keeps the tests whose markers name ID and deselects the rest."""
        # Arrange
        scratch = tmp_path / "test_probe.py"
        scratch.write_text(
            "import pytest\n\n\n"
            '@pytest.mark.rule("rules-registry")\n'
            "def test_tagged():\n"
            '    """A probe that pins the registry rule."""\n\n\n'
            "def test_untagged():\n"
            '    """A probe that pins nothing."""\n',
            encoding="utf-8",
        )

        # Act
        result = _child_pytest(scratch, "--rule", "rules-registry")

        # Assert
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        assert "test_tagged" in result.stdout, "the tagged test is collected"
        assert "test_untagged" not in result.stdout, "the untagged test is not"
        assert "1 deselected" in result.stdout, "and is reported as deselected"

    def test_rule_with_an_unknown_id_is_a_usage_error(self, tmp_path):
        """``--rule`` with an id no source holds is refused before anything runs."""
        # Arrange
        scratch = tmp_path / "test_probe.py"
        scratch.write_text(
            'def test_plain():\n    """A probe."""\n',
            encoding="utf-8",
        )

        # Act
        result = _child_pytest(scratch, "--rule", "no-such-rule")

        # Assert
        assert result.returncode == 4, f"{result.stdout}\n{result.stderr}"
        assert "--rule no-such-rule: no id source holds it" in result.stderr
