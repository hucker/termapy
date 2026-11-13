# Traceability

**Status: experimental.** Traceability is new (2026-11-08): the marker, the id
sources and the matrix may change shape as requirement documents join, and what
is traced today is the rule registry and one spec, not requirements. The matrix
is an entry point, never a coverage claim.

How a test says what it pins, and where that is going. The first half is what
runs today; the second is the plan, and says so.

## Today: a test names its rule

A test that pins a rule carries a marker naming the rule's id:

```python
@pytest.mark.rule("bare-declared")
def test_bare_picker_commands_open_their_picker(app):
    """A bare /cfg in the TUI opens the config picker and never reaches the handler."""
```

One marker per rule, stacked when a test pins two; a class marker covers its
methods and `pytestmark = pytest.mark.rule("...")` covers a module, a list when
the module pins several (a second assignment would replace the first, and the
guard says so). Most tests pin no rule and carry no marker. The docstring header stays prose, what the
test pins in words; the marker carries the id, so the id has one owner.

An id is one of:

- a row of the rule registry, [`rules.md`](rules.md): the rules the code relies
  on, each with a kind (a **guarantee** has a test that enforces it everywhere,
  an **assumption** holds by review) and the test or tests that pin it;
- a section of a spec that labels its sections with ids, today
  [`param-spec-implementation.md`](param-spec-implementation.md) (`param-...`).

The sources are one table, `SOURCES` in `tests/traceability.py`, each with the
parser that finds its ids. A new document with ids is one more row there.

What keeps it honest, all in `uv run pytest`:

- **Collection refuses a marker that names nothing.** `tests/conftest.py` reads
  the sources once and ends the session, naming the test and the id, when a
  marker names an id no source holds or takes anything but one id. A typo
  cannot tag a test with a rule nobody wrote down, and a retired rule cannot
  keep its tests.
- **A registry row and its tests agree both ways** (registry `rules-registry`,
  `tests/test_architecture.py`): every test a row names exists and carries the
  row's marker, a guarantee names at least one, and every guard in that file
  carries a registered id.
- **A misspelled marker name fails** under `--strict-markers`, which is in the
  suite's `addopts`.
- **The matrix is current.** `tests/test_traceability.py` fails when
  `traceability-matrix.md` no longer matches the markers (registry
  `traceability-matrix-generated`).

The matrix, [`traceability-matrix.md`](traceability-matrix.md), is generated
from the markers by `scripts/sync_traceability.py`: first a table of the ids no
test pins, then one section per id source, the registry today and each spec,
with every test that pins each id by file and the tests the registry row names
marked as the guard or its probe. A requirements file added to `SOURCES` gets
its own section with no change to the script. It is never edited by hand:

```sh
uv run python scripts/sync_traceability.py
```

A registry row names only the guard, the test whose failure means the rule is
broken anywhere, and its probe; the instance tests that once filled the
`enforced by` column are the matrix's, read from their markers.

To work on one rule:

```sh
uv run pytest --rule bare-declared -n 0 --no-cov   # every test that pins it
uv run pytest --rule bare-declared --co -q         # just list them
```

To add a rule: a row in `rules.md` (an assumption until its guard exists), then
the marker on the tests that pin it, the guard's included. To retire one:
remove the row and the markers in the same change; collection names any you
miss.

## Plan: requirements, tagged the same way

The registry is the entry point, not the end: it holds the rules the code
relies on, which is not the same as what the product must do. The direction
is a set of requirement documents, each a Markdown file under `docs/`, each
requirement a short section with a stable id, traced into tests by the same
marker and into code by a reference still to be chosen:

- **User requirements**: what a person at the terminal, or an agent over MCP,
  can do and what they see (today spread over `help/` and the command
  declarations).
- **Architectural requirements**: the shape the code keeps, most of which the
  registry's `structure` and `concurrency` rows already state.
- **Performance requirements**: what termapy keeps up with and how fast it
  answers (the throughput numbers in `help/ports.md` are the first measurements).
- **Security requirements**: what an MCP client can never reach, which the
  registry's `mcp-*` and `os-never-under-mcp` rows state today.

Each file becomes a row of `SOURCES` when it exists; a requirement with no
tagged test is then a question the matrix can ask, and the answer is a test or
a stated reason. None of these files exists yet, and nothing here claims any
coverage: the mechanism is in place so that each one can be added as a document
and a marker, not as a new system.
