"""Structural rules checked against the source tree itself.

These read the SOURCE rather than exercising behavior, because the rules they
enforce are invisible at runtime: an import that crosses a layer boundary works
fine until someone has to unpick it months later.  Keeping them here rather
than in a CI-only grep means they run in ``uv run pytest``, which is the loop
that actually happens before a commit -- a guard you only meet after pushing is
a guard you meet too late.
"""

# cspell:ignore ture -- quoted as the misspelled "true" that once silently meant off

from __future__ import annotations

import ast
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from traceability import (
    RULE_ID,
    TEST_REF,
    MarkIndex,
    index_module,
    index_tests,
    known_ids,
    marker_problems,
    registry_rows,
)

import termapy
from termapy.defaults import cfg_key_path, cfg_key_paths, cfg_value
from termapy.plugins.capabilities import STATE_MESSAGES

SRC = Path(termapy.__file__).parent
BUILTINS_DIR = SRC / "builtins"

# (module, imported name) pairs core is permitted to import from builtins/.
#
# Two things earn a place here, and nothing else:
#   1. A PUBLISHED plugin function -- the plugin's deliberate entry point for
#      the app, named without a leading underscore and documented as such.
#   2. A helper MODULE that lives under commands/ for proximity but is not a
#      plugin at all (no COMMAND dict), so importing it crosses no boundary.
#
# A private handler or private state (``_handler_clear``, ``_VARS``) never
# belongs here: a plugin's privates carry no contract with the app.
ALLOWED_CORE_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        # (1) Published plugin functions.
        ("termapy.builtins.commands.find", "dismiss"),
        # (2) Helper modules under commands/ that are not plugins.
        ("termapy.builtins.commands._cfg_icon", "retire_launcher"),
        ("termapy.builtins.commands", "_run_record"),
    }
)


def _core_python_files() -> list[Path]:
    """Every .py file in the package except the plugins themselves."""
    return [
        path
        for path in SRC.rglob("*.py")
        if BUILTINS_DIR not in path.parents and path.parent != BUILTINS_DIR
    ]


def _builtins_imports(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, module, imported_name) for each builtins import.

    Uses ``ast`` rather than a regex so a parenthesized multi-line import --
    which is exactly the shape the variable-engine imports had -- yields one
    entry per name, and so text inside strings or comments never matches.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "termapy.builtins" or module.startswith("termapy.builtins."):
                found.extend((node.lineno, module, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(
                (node.lineno, alias.name, "")
                for alias in node.names
                if alias.name.startswith("termapy.builtins")
            )
    return found


@pytest.mark.rule("core-imports-published-only")
def test_core_does_not_import_from_plugins():
    """Core may use a plugin's published surface, never its internals.

    The plugin system is built on core-below-plugins.  When infrastructure is
    misfiled under ``builtins/``, core has to import upward to reach it, and
    the inversion is silent -- that is how the ``$(NAME)`` engine ended up in
    a plugin with five core modules importing it.
    """
    # Arrange
    violations: list[str] = []

    # Act
    for path in _core_python_files():
        for lineno, module, name in _builtins_imports(path):
            if (module, name) in ALLOWED_CORE_IMPORTS:
                continue
            rel = path.relative_to(SRC.parent)
            imported = f"{module}.{name}" if name else module
            violations.append(f"{rel}:{lineno}  imports  {imported}")

    # Assert
    assert violations == [], (
        "core code must not import from builtins/ outside the allowlist:\n  "
        + "\n  ".join(sorted(violations))
        + "\n\nIf core needs it at startup or on the dispatch path, it IS core:"
        "\nmove it out of builtins/ and leave the command a thin surface over"
        "\nit (see variables.py <- builtins/commands/var.py, and"
        "\nport_control.py <- builtins/commands/port.py)."
        "\n\nIf it is genuinely plugin behavior, call the plugin's PUBLISHED"
        "\nfunction and add it to ALLOWED_CORE_IMPORTS above -- never a private"
        "\nhandler or private state."
    )


def test_allowlist_has_no_stale_entries():
    """Every allowlisted import still exists, so the list can't rot.

    Without this, a removed import leaves a permanent hole in the guard that
    silently re-permits the thing it was granted for.
    """
    # Arrange
    actual: set[tuple[str, str]] = set()

    # Act
    for path in _core_python_files():
        actual.update((module, name) for _, module, name in _builtins_imports(path))

    # Assert
    stale = sorted(ALLOWED_CORE_IMPORTS - actual)
    assert stale == [], (
        "ALLOWED_CORE_IMPORTS lists imports that no longer exist; drop them so "
        "the allowlist stays a description of reality:\n  "
        + "\n  ".join(f"{module}.{name}" for module, name in stale)
    )


# ── Plugins import published names only ──────────────────────────────────────
#
# The mirror of the guard above.  The built-ins are the working examples a
# plugin author copies, so what they import is what every plugin may import:
# core's published functions, never a leading-underscore name, and never
# another plugin.  A command's own private helper file (``_cfg_icon``,
# ``_run_record``, ``_xmodem_handlers``, ``_crc_verbs``: a ``_name`` module
# under commands/, not a plugin) is the one place a private name is shared.


def _plugin_python_files() -> list[Path]:
    """Every .py file under builtins/: the plugins and their helper modules."""
    return sorted(BUILTINS_DIR.rglob("*.py"))


def _is_helper_module(module: str) -> bool:
    """A ``_name`` module is a command's private helper file, not a plugin."""
    return module.rsplit(".", 1)[-1].startswith("_")


def _has_private_part(dotted: str) -> bool:
    """Whether any component of a dotted name starts with an underscore."""
    return any(part.startswith("_") for part in dotted.split("."))


def _unpublished_imports(path: Path, root: Path) -> list[tuple[int, str]]:
    """Each import in a plugin file that reaches an unpublished name, as ``(lineno, what)``.

    Caught by ``ast``, so strings and comments never match: an import from
    another plugin (a ``termapy.builtins`` module that is not a helper
    file), and an import of a leading-underscore name or module from
    anywhere else in termapy.  A relative import is resolved against the
    file's package under ``root``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = path.relative_to(root).with_suffix("").parts[: -node.level]
                module = ".".join([*parts, module]) if module else ".".join(parts)
            if not module.startswith("termapy"):
                continue
            if module.startswith("termapy.builtins"):
                if not _is_helper_module(module):
                    found.extend(
                        (node.lineno, f"{module}.{alias.name} (another plugin)")
                        for alias in node.names
                    )
                continue
            if _has_private_part(module):
                found.append((node.lineno, module))
            found.extend(
                (node.lineno, f"{module}.{alias.name}")
                for alias in node.names
                if alias.name.startswith("_")
            )
        elif isinstance(node, ast.Import):
            found.extend(
                (node.lineno, alias.name)
                for alias in node.names
                if alias.name.startswith("termapy") and _has_private_part(alias.name)
            )
    return found


@pytest.mark.rule("infra-not-under-builtins")
@pytest.mark.rule("plugins-import-published-only")
def test_plugins_import_published_only():
    """A plugin imports published names only, and from builtins/ only a helper module.

    Two commands talk through ``ctx.internal.dispatch``, not an import, so
    the load-order override reaches every command; a helper two plugins
    share is core (``edit_command`` in ``folder_ops``, ``find_matches`` in
    ``scripting``).  Until 2026-10-24 four plugins imported
    ``help._show_command_help``, seven imported another plugin's export,
    and two reached a core module's privates.
    """
    # Arrange / Act
    violations = [
        f"{path.relative_to(SRC.parent)}:{lineno}  imports  {what}"
        for path in _plugin_python_files()
        for lineno, what in _unpublished_imports(path, SRC.parent)
    ]

    # Assert
    assert violations == [], (
        "a plugin imports an unpublished name:\n  "
        + "\n  ".join(violations)
        + "\n\nTo use another command, dispatch it (ctx.internal.dispatch('help run'))."
        "\nA helper two plugins share is core: move it beside its subject"
        "\n(folder_ops, scripting, help_dynamic, markup) and import it from there."
        "\nA leading-underscore name is private; publish what the plugin needs."
    )


def test_plugins_import_guard_fires_on_a_probe(tmp_path):
    """The guard sees a plugin import, a private name, a private module and a relative one; a helper and a public name pass."""
    # Arrange -- a plugin file in a package shaped like the real tree
    probe = tmp_path / "termapy" / "builtins" / "commands" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(
        "from termapy.builtins.commands.help import show_command_help\n"
        "from termapy.protocol.crc import _generic_crc\n"
        "from termapy.usb import _vendors_full\n"
        "import termapy.usb._vendors_full\n"
        "from . import grep\n"
        "from termapy.builtins.commands._probe_helper import _secret\n"
        "from termapy.scripting import parse_bool\n",
        encoding="utf-8",
    )

    # Act
    actual = [what for _, what in _unpublished_imports(probe, tmp_path)]

    # Assert
    expected = [
        "termapy.builtins.commands.help.show_command_help (another plugin)",
        "termapy.protocol.crc._generic_crc",
        "termapy.usb._vendors_full",
        "termapy.usb._vendors_full",
        "termapy.builtins.commands.grep (another plugin)",
    ]
    assert actual == expected, (
        "each unpublished reach is named once; the helper and public names pass"
    )


# ── Engine-internal IO primitives ────────────────────────────────────────────

# ``ctx.io._write`` / ``._write_markup`` / ``._write_segments`` are the raw
# primitives the frontends implement.  Handlers use the semantic channels
# (result / output / status / the ``*_segments`` pair) instead, so the frontend
# decides how each kind of message is rendered.
IO_PRIMITIVES: frozenset[str] = frozenset({"_write", "_write_markup", "_write_segments"})


def _plugin_python_files() -> list[Path]:
    """Every .py file under builtins/ -- the plugins themselves."""
    return sorted(BUILTINS_DIR.rglob("*.py"))


@pytest.mark.rule("io-semantic-channels")
def test_plugins_do_not_use_engine_io_primitives():
    """Handlers must reach the terminal through the semantic channels.

    Matches on the ATTRIBUTE, not on the text ``ctx.io._write``, which is what
    the equivalent CI grep keys on.  That matters twice over: a comment or
    docstring quoting the primitive no longer trips the check (documentation
    should be free to name the thing it is warning about), and aliasing it
    first -- ``io = ctx.io`` then ``io._write(...)`` -- no longer slips past.
    """
    # Arrange
    violations: list[str] = []

    # Act
    for path in _plugin_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC.parent)
        for node in ast.walk(tree):
            # Direct attribute use, however the object was obtained.
            if isinstance(node, ast.Attribute) and node.attr in IO_PRIMITIVES:
                violations.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")
            # The getattr() back door.
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in IO_PRIMITIVES
            ):
                violations.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")

    # Assert
    assert violations == [], (
        "handler code in builtins/ must not touch the raw write primitives:\n  "
        + "\n  ".join(violations)
        + "\n\nUse the semantic channels instead -- result / output / status"
        "\n(or their _markup variants).  The underscore-prefixed primitives are"
        "\nreserved for engine-internal use, so that the frontend stays in"
        "\ncharge of how each kind of message is rendered."
    )


# ── A plugin never asks who is reading ───────────────────────────────────────
#
# ``ctx.wants_data`` says a caller wants records, not prose (``--json``,
# ``/term.request``, MCP).  Only dispatch reads it: a handler returns a view
# (``CmdResult.table`` / ``.record``) and dispatch prints it for a person or
# hands the data on.  A handler that branched on it printed its own prose
# and kept its own data shape, and seven of them never became views; the
# last was found when ``/port.list`` printed nothing at ``--quiet``.


def _wants_data_reads(tree: ast.AST) -> list[int]:
    """The line of every read of a ``wants_data`` attribute in ``tree``, the ``getattr`` back door included."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "wants_data":
            lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "wants_data"
        ):
            lines.append(node.lineno)
    return lines


@pytest.mark.rule("plugins-never-read-wants-data")
def test_plugins_never_read_wants_data():
    """No module under builtins/ reads ctx.wants_data; a handler returns a view and dispatch chooses."""
    # Arrange / Act
    violations = [
        f"{path.relative_to(SRC.parent)}:{line}"
        for path in _plugin_python_files()
        for line in _wants_data_reads(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "a plugin never asks who is reading:\n  "
        + "\n  ".join(violations)
        + "\n\nReturn a view (CmdResult.table for a list, CmdResult.record for a"
        "\npage) and let dispatch print it for a person or hand the data on."
    )


def test_wants_data_guard_fires_on_a_probe():
    """The guard flags a direct read, a read through an alias and the getattr back door, and passes a comment and other attributes."""
    # Arrange
    probe = (
        "# ctx.wants_data in a comment is fine\n"
        "def _handler(ctx, args):\n"
        "    if ctx.wants_data:\n"
        "        return 1\n"
        "    c = ctx\n"
        "    flag = c.wants_data\n"
        '    other = getattr(ctx, "wants_data")\n'
        "    ctx.io.output(args)\n"
    )

    # Act
    found = _wants_data_reads(ast.parse(probe))

    # Assert
    assert found == [3, 6, 7], (
        "the three reads, by line; the comment and ctx.io.output are not reads"
    )


# ── A value is typed, never spelled at the source ────────────────────────────
#
# A ``CmdResult.value`` carries its natural type and the REPL spells it at its
# boundary (``scripting.value_text``).  Two idioms spelled one at the source
# and recurred in forty handlers before the 2026-11-08 audit: ``str(len(...))``
# for a count and ``"on" if x else "off"`` for a switch.  Those two shapes are
# what the guard can see; a ``str(n)`` over a number it cannot tell from a
# ``str(path)``, which is review.

_SPELLED_SWITCHES = frozenset({"on", "off", "yes", "no", "true", "false", "1", "0"})


def _spelled_values(tree: ast.AST) -> list[int]:
    """The line of every ``CmdResult`` call whose ``value=`` is ``str(len(...))`` or a spelled switch."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_cmdresult_call(node.func):
            continue
        for keyword in node.keywords:
            if keyword.arg != "value":
                continue
            value = keyword.value
            counted = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "str"
                and len(value.args) == 1
                and isinstance(value.args[0], ast.Call)
                and isinstance(value.args[0].func, ast.Name)
                and value.args[0].func.id == "len"
            )
            switched = (
                isinstance(value, ast.IfExp)
                and isinstance(value.body, ast.Constant)
                and isinstance(value.orelse, ast.Constant)
                and value.body.value in _SPELLED_SWITCHES
                and value.orelse.value in _SPELLED_SWITCHES
            )
            if counted or switched:
                lines.append(node.lineno)
    return lines


def _is_cmdresult_call(func: ast.expr) -> bool:
    """``CmdResult(...)`` or ``CmdResult.<anything>(...)``."""
    if isinstance(func, ast.Name):
        return func.id == "CmdResult"
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "CmdResult"
    )


@pytest.mark.rule("value-typed")
def test_a_value_is_not_spelled_at_the_source():
    """No CmdResult value is a str(len(...)) count or an "on" if x else "off" switch; the REPL spells a value at its boundary."""
    # Arrange / Act
    violations = [
        f"{path.relative_to(SRC.parent)}:{line}"
        for path in _package_python_files()
        for line in _spelled_values(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "a value carries its type; the REPL spells it (scripting.value_text):\n  "
        + "\n  ".join(violations)
        + "\n\nAnswer value=len(...) or value=<bool>; a person still reads the count"
        "\nor on/off, through value_text at the capture, the poll and the state line."
    )


def test_spelled_value_guard_fires_on_a_probe():
    """The guard flags the two idioms on any CmdResult constructor and passes a path's str(), a typed count and a plain text."""
    # Arrange
    probe = (
        "def a(ctx, args):\n"
        "    return CmdResult.ok(value=str(len(rows)))\n"
        "def b(ctx, args):\n"
        '    return CmdResult.record(data, rows, value="on" if flag else "off")\n'
        "def c(ctx, args):\n"
        '    return CmdResult(value="yes" if ok else "no")\n'
        "def d(ctx, args):\n"
        "    return CmdResult.ok(value=str(path))\n"
        "def e(ctx, args):\n"
        "    return CmdResult.ok(value=len(rows))\n"
        "def f(ctx, args):\n"
        '    return CmdResult.ok(value="ready" if ok else "busy")\n'
    )

    # Act
    found = _spelled_values(ast.parse(probe))

    # Assert
    assert found == [2, 4, 6], (
        "the count and the two switches; a path, a typed count and other text pass"
    )


# ── UI-layer confinement ─────────────────────────────────────────────────────

# Textual may only be imported by the UI layer.  Everything else -- the REPL
# engine, the plugin system, the serial stack, and every built-in command --
# stays importable in CLI and MCP hosts, which never load Textual at all.
UI_LAYER_PACKAGES: frozenset[str] = frozenset({"dialogs", "widgets"})
UI_LAYER_MODULES: frozenset[str] = frozenset(
    {
        "app.py",
        "capture_view.py",
        "info_views.py",
        "palette_provider.py",
        "proto_debug.py",
        "title_bar.py",
    }
)


def _imports_package(tree: ast.AST, root: str) -> bool:
    """True when the module imports ``root`` or anything beneath it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == root or alias.name.startswith(f"{root}.") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == root or module.startswith(f"{root}."):
                return True
    return False


def _is_ui_layer(rel: Path) -> bool:
    """True when this path is one of the modules allowed to import Textual."""
    if rel.parts[0] in UI_LAYER_PACKAGES:
        return True
    return len(rel.parts) == 1 and rel.name in UI_LAYER_MODULES


@pytest.mark.rule("ui-layer-textual")
def test_textual_stays_in_the_ui_layer():
    """Only the UI layer may import Textual.

    CLAUDE.md names this layer, and the list was maintained by hand.  Pinning
    it here makes it self-verifying: a new Textual import fails until the
    module is either kept Textual-free or consciously added above.  The
    complementary runtime check lives in test_cli.py, which asserts that
    importing ``termapy.entry`` pulls in no textual module at all -- that one
    catches TRANSITIVE imports, which this static pass cannot see.
    """
    # Arrange
    offenders: list[str] = []

    # Act
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC)
        if _is_ui_layer(rel):
            continue
        if _imports_package(ast.parse(path.read_text(encoding="utf-8")), "textual"):
            offenders.append(str(rel).replace("\\", "/"))

    # Assert
    assert offenders == [], (
        "these modules import Textual but are not in the UI layer:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither keep the module Textual-free (a Textual-dependent command"
        "\nbelongs in an app.py register_hook, not in builtins/), or add it to"
        "\nUI_LAYER_MODULES here and to the layer list in CLAUDE.md."
    )


def test_ui_layer_list_has_no_stale_entries():
    """Every module named in UI_LAYER_MODULES still exists.

    A rename would otherwise leave a dead entry, and the real file would be
    silently outside the list it was meant to be inside.
    """
    # Arrange / Act
    missing = sorted(name for name in UI_LAYER_MODULES if not (SRC / name).is_file())

    # Assert
    assert missing == [], "UI_LAYER_MODULES names modules that no longer exist: " + ", ".join(
        missing
    )


@pytest.mark.rule("engine-transport-agnostic")
def test_engine_modules_have_no_serial_dependency():
    """plugins/, scripting.py, symbols/ and memory.py stay transport-agnostic.

    ``plugins/`` defines the command/context API, ``scripting.py`` is pure
    functions, ``symbols/`` is library-shaped like ``protocol/`` (a symbol
    table describes a firmware build, not a wire), and ``memory.py`` moves
    bytes over an injected exchange; none should know that a serial port
    exists.  Keeping pyserial out of them is what lets a
    non-serial frontend reuse the whole command layer.
    """
    # Arrange
    targets = [
        *(SRC / "plugins").rglob("*.py"),
        *(SRC / "symbols").rglob("*.py"),
        SRC / "scripting.py",
        SRC / "memory.py",
    ]
    offenders: list[str] = []

    # Act
    for path in sorted(targets):
        if _imports_package(ast.parse(path.read_text(encoding="utf-8")), "serial"):
            offenders.append(str(path.relative_to(SRC)).replace("\\", "/"))

    # Assert
    assert offenders == [], (
        "these modules import pyserial but must stay transport-agnostic:\n  "
        + "\n  ".join(offenders)
    )


# ── Boolean vocabulary ───────────────────────────────────────────────────────

# Token constants that mark a hand-rolled boolean comparison.  Comparing
# user input against these is how ``/term.request yes`` got rejected and
# ``TERMAPY_OS_CMD_ENABLED=ture`` silently meant off; the one reader is
# ``scripting.parse_bool``, directly or through ``parse_bool_setting``
# and ``ParamSpec(type="bool")``.
_BOOL_TOKENS: frozenset[str] = frozenset({"on", "off", "true", "false", "yes", "no"})

# The whole package is scanned, minus vendor/ and this allowlist, each
# entry with the reason it may name the tokens.  An entry whose reason
# has gone (the file no longer names a token) fails the companion test
# below, so the list only shrinks.  scripting.py needs no entry: its
# vocabulary is a table bound to a name, and the matcher flags literal
# tokens at the point of comparison, which is exactly the shape the rule
# forbids -- a second table bound to a name elsewhere is the one blind
# spot, left to review.
_BOOL_SCAN_ALLOWLIST: dict[str, str] = {
    "termapy/demo.py": "the simulated device's own AT+LED on|off grammar, not termapy's",
}


def _bool_rel(path: Path) -> str:
    """A module's path as the scan names it: ``termapy/...`` with forward slashes."""
    return str(path.relative_to(SRC.parent)).replace("\\", "/")


def _bool_scan_targets() -> list[Path]:
    """Every module under src/termapy except the vendored tree and the allowlist."""
    return sorted(
        path
        for path in SRC.rglob("*.py")
        if "vendor" not in path.relative_to(SRC).parts
        and _bool_rel(path) not in _BOOL_SCAN_ALLOWLIST
    )


def _bool_token_violations(tree: ast.AST, rel: str) -> list[str]:
    """Every place ``rel`` compares against a bool token or builds an on/off enum."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            constants: list[ast.Constant] = []
            for operand in operands:
                if isinstance(operand, ast.Constant):
                    constants.append(operand)
                elif isinstance(operand, (ast.Tuple, ast.List, ast.Set)):
                    constants.extend(
                        element for element in operand.elts if isinstance(element, ast.Constant)
                    )
                elif (
                    isinstance(operand, ast.Call)
                    and isinstance(operand.func, ast.Name)
                    and operand.func.id in ("frozenset", "set", "tuple", "list")
                    and operand.args
                    and isinstance(operand.args[0], (ast.Tuple, ast.List, ast.Set))
                ):
                    # ``x in frozenset({...})``: the literal wrapped in a call.
                    constants.extend(
                        element
                        for element in operand.args[0].elts
                        if isinstance(element, ast.Constant)
                    )
            if any(
                isinstance(constant.value, str) and constant.value.lower() in _BOOL_TOKENS
                for constant in constants
            ):
                found.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "EnumValue"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).lower() in ("on", "off")
        ):
            found.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")
    return found


@pytest.mark.rule("bool-one-vocabulary")
def test_booleans_go_through_parse_bool():
    """Every text-to-boolean read under src/termapy goes through parse_bool; no module outside the allowlist compares a token to the vocabulary.

    One vocabulary, one reader (see CLAUDE.md): ``parse_bool`` directly,
    or through ``parse_bool_setting`` and the ``bool`` param type.  A
    literal comparison accepts only the tokens the author remembered
    (``/term.request yes`` was rejected) and treats a typo as False
    instead of erroring (``TERMAPY_OS_CMD_ENABLED=ture`` meant off).
    """
    # Arrange / Act
    violations: list[str] = []
    for path in _bool_scan_targets():
        violations.extend(
            _bool_token_violations(ast.parse(path.read_text(encoding="utf-8")), _bool_rel(path))
        )

    # Assert
    assert violations == [], (
        "boolean tokens must go through parse_bool / ParamSpec(type='bool') / "
        "parse_bool_setting, never literal comparisons:\n  " + "\n  ".join(violations)
    )


def test_boolean_guard_fires_on_a_probe():
    """The guard actually detects the patterns it exists to forbid."""
    # Arrange -- the three shapes the audit found in the wild, plus the
    # set-literal-in-a-call shape env_flags used
    probe = (
        "x = tok == 'on'\n"
        "y = tok in ('on', 'off', 'toggle')\n"
        "z = EnumValue('on')\n"
        "w = tok in frozenset({'1', 'true'})\n"
    )

    # Act
    found = _bool_token_violations(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 4, "a comparison, a membership test, an enum and a wrapped set all flagged"


def test_boolean_allowlist_has_no_stale_entries():
    """Every allowlisted file exists, is left out of the scan, and still names a token, so an entry never outlives its reason."""
    # Arrange / Act
    scanned = {_bool_rel(path) for path in _bool_scan_targets()}
    stale: list[str] = []
    for rel, reason in _BOOL_SCAN_ALLOWLIST.items():
        path = SRC.parent / rel
        if not path.exists():
            stale.append(f"{rel}: no such file ({reason})")
        elif rel in scanned:
            stale.append(f"{rel}: scanned despite the allowlist")
        elif not _bool_token_violations(ast.parse(path.read_text(encoding="utf-8")), rel):
            stale.append(f"{rel}: names no token any more; drop the entry ({reason})")

    # Assert
    assert stale == [], "\n  ".join(["the allowlist only shrinks:", *stale])
    assert not any(rel.startswith("termapy/vendor/") for rel in scanned), (
        "the vendored tree is never scanned"
    )


# ── Data folders: one creator ────────────────────────────────────────────────

# Files where a raw ``.mkdir(`` is legitimate because the folder is NOT a
# per-config data folder (``folders.FOLDERS``): the cfg root and the demo
# root (config.py, the picker's config editor), the OS app-state / app-config
# dirs, the desktop-launcher files, the MCP host's own ``mcp/``, and
# folders.py itself, where ``ensure_folder`` lives.  ``devices/`` is here for
# the NESTED vendor/type folders inside a device library: ``lib/`` itself is
# a data folder and goes through ``ensure_folder``, but the tree under it is
# the library's own shape, which only that module knows.  A data folder comes into
# being through ``folders.ensure_folder`` and goes away through
# ``folders.prune_empty_folders``, and through nothing else -- that is what
# keeps "reads never create, writes always create" true everywhere at once.
ALLOWED_RAW_MKDIR: frozenset[str] = frozenset(
    {
        "termapy/app_dirs.py",
        "termapy/builtins/commands/_cfg_icon.py",
        "termapy/builtins/commands/app.py",
        # The app-wide running/ folder of breadcrumbs, not a config's data folder.
        "termapy/breadcrumbs.py",
        "termapy/config.py",
        "termapy/devices/__init__.py",
        # /firmware.copy creates the user's own output folder, not a data folder.
        "termapy/firmware/__init__.py",
        "termapy/folders.py",
    }
)


def _raw_mkdir_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every ``<expr>.mkdir(...)`` call in ``rel``, whatever the receiver."""
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mkdir"
    ]


def _package_python_files() -> list[Path]:
    """Every .py file in the package except the vendored tree."""
    return sorted(path for path in SRC.rglob("*.py") if "vendor" not in path.relative_to(SRC).parts)


def _raw_mkdir_files() -> dict[str, list[str]]:
    """``{rel: [violation lines]}`` for every package file with a raw mkdir."""
    found: dict[str, list[str]] = {}
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        calls = _raw_mkdir_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
        if calls:
            found[rel] = calls
    return found


@pytest.mark.rule("folders-ensure-only")
def test_data_folders_are_created_only_by_ensure_folder():
    """No code outside the allowlist creates a folder with a raw ``mkdir``.

    A data folder exists while something is in it (see ``folders``): every
    writer calls ``ensure_folder`` at the write, ``prune_empty_folders``
    removes empty ones at config load and app stop.  A raw ``mkdir`` on a
    data folder is either a second creator the prune does not know about
    or, eagerly on a read path, the ten-empty-folders layout this rule
    replaced.
    """
    # Arrange / Act
    violations = [
        line
        for rel, calls in _raw_mkdir_files().items()
        if rel not in ALLOWED_RAW_MKDIR
        for line in calls
    ]

    # Assert
    assert violations == [], (
        "a data folder is created by folders.ensure_folder, never a raw mkdir:\n  "
        + "\n  ".join(violations)
        + "\n\nCall ensure_folder(folder) at the write.  If the folder is genuinely"
        "\nnot a per-config data folder, add the FILE to ALLOWED_RAW_MKDIR above."
    )


# Files that call json.dump for a file that is not a config, with why.  Only
# shrinks: a config is written by config.write_config_file.
ALLOWED_JSON_DUMP: dict[str, str] = {
    "termapy/app_dirs.py": "the app's own state file, not a config",
    "termapy/protocol/runner.py": "a proto test's results file, not a config",
}


def _json_dump_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every ``json.dump(...)`` call in ``rel``, except inside ``config.write_config_file``."""
    exempt = {
        id(node)
        for function in ast.walk(tree)
        if rel == "termapy/config.py"
        and isinstance(function, ast.FunctionDef)
        and function.name == "write_config_file"
        for node in ast.walk(function)
    }
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dump"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id.endswith("json")
        and id(node) not in exempt
    ]


@pytest.mark.rule("cfg-written-in-order")
def test_config_files_are_written_by_one_writer():
    """Only ``config.write_config_file`` calls ``json.dump``, outside the allowlist.

    It lays a config out in ``DEFAULT_CFG``'s order (``ordered_cfg``), so a
    file reads group by group whichever path wrote it: load's migration
    write-back, ``create_config`` and the config editor's Save.  A second
    writer would put the keys wherever its dict had them.
    """
    # Arrange / Act
    violations = []
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        if rel not in ALLOWED_JSON_DUMP:
            violations += _json_dump_calls(ast.parse(path.read_text(encoding="utf-8")), rel)

    # Assert
    assert violations == [], (
        "a config file is written by config.write_config_file, never json.dump:\n  "
        + "\n  ".join(violations)
        + "\n\nIf the file is genuinely not a config, add it to ALLOWED_JSON_DUMP above."
    )


# Files that type a folder glob for a folder FOLDERS does not hold, with why.
# Only shrinks: a data folder's glob is read from FOLDER_PATTERNS.
ALLOWED_TYPED_GLOBS: dict[str, str] = {
    "termapy/protocol/crc.py": "scans the package's built-in crc/ modules, not a data folder",
}


def _typed_constants(tree: ast.AST, rel: str, values: frozenset[str]) -> list[str]:
    """Every string constant in ``rel`` that is one of ``values`` (``"*.run"``, ``".pro"``)."""
    return [
        f"{rel}:{node.lineno}  {node.value!r}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value in values
    ]


def _typed_outside(values: frozenset[str], exempt: set[str]) -> list[str]:
    """``values`` typed anywhere in the package but the ``exempt`` files."""
    found: list[str] = []
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        if rel not in exempt:
            found += _typed_constants(ast.parse(path.read_text(encoding="utf-8")), rel, values)
    return found


@pytest.mark.rule("folder-globs-from-table")
def test_folder_globs_are_read_from_the_table():
    """A data folder's glob comes from FOLDER_PATTERNS, never typed.

    ``folders.FOLDERS`` says once that run/ holds ``.run`` files.  A
    ``"*.run"`` typed at a reader is a second copy that drifts when the
    table changes: ``app.py`` read the table in one method and typed the
    glob in another.  Only ``folders.py``, the owner, spells them out.
    """
    # Arrange
    from termapy.folders import FOLDER_PATTERNS

    patterns = frozenset(pattern for pattern in FOLDER_PATTERNS.values() if pattern != "*")

    # Act
    violations = _typed_outside(patterns, {"termapy/folders.py", *ALLOWED_TYPED_GLOBS})

    # Assert
    assert violations == [], (
        "a data folder's glob is FOLDER_PATTERNS[<folder>], never typed:\n  "
        + "\n  ".join(violations)
        + "\n\nIf the folder is not in folders.FOLDERS, add the FILE to ALLOWED_TYPED_GLOBS."
    )


@pytest.mark.rule("folder-globs-from-table")
def test_typed_glob_guard_fires_on_a_probe():
    """The guard flags a typed folder glob and passes a read from the table."""
    # Arrange
    probe = 'folder.glob("*.run")\nfolder.glob(FOLDER_PATTERNS[RUN])\n'

    # Act
    found = _typed_constants(ast.parse(probe), "probe", frozenset({"*.run"}))

    # Assert
    assert len(found) == 1, "the typed glob is flagged; the table read is not"


@pytest.mark.rule("folder-exts-from-table")
def test_termapy_extensions_are_read_from_the_table():
    """termapy's own file types, .run and .pro, come from FOLDER_EXTS.

    Checking a suffix (``ext == ".run"``), adding one
    (``new_file_name(name, FOLDER_EXTS[RUN])``) or probing for one
    (``default_file_name(path, FOLDER_EXTS[PROTO])``) reads the table, so
    ``FolderSpec("run", ".run")`` is the one place the type is said.  Only
    the two types termapy invented: ``.py``, ``.csv`` and ``.json`` are
    general formats a module may name for its own reasons (a capture's
    CSV output is not prof/).
    """
    # Arrange
    from termapy.folders import FOLDER_EXTS, PROTO, RUN

    exts = frozenset({FOLDER_EXTS[RUN], FOLDER_EXTS[PROTO]})

    # Act
    violations = _typed_outside(exts, {"termapy/folders.py"})

    # Assert
    assert violations == [], (
        "termapy's own file types are FOLDER_EXTS[RUN] / FOLDER_EXTS[PROTO], never typed:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("folder-exts-from-table")
def test_typed_ext_guard_fires_on_a_probe():
    """The guard flags a typed .run or .pro and passes a read from the table and prose."""
    # Arrange
    probe = (
        'if name.endswith(".run"): pass\n'
        'name = name + ".pro"\n'
        "if name.endswith(FOLDER_EXTS[RUN]): pass\n"
        '"""Saves a .run script."""\n'
    )

    # Act
    found = _typed_constants(ast.parse(probe), "probe", frozenset({".run", ".pro"}))

    # Assert
    assert len(found) == 2, "both typed extensions; not the table read, not the prose"


def test_raw_mkdir_guard_fires_on_a_probe():
    """The guard actually detects the shapes it exists to forbid."""
    # Arrange -- a bare receiver and a chained one, as the eager loop had
    probe = (
        "(d / sub).mkdir(exist_ok=True)\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "ensure_folder(path.parent)\n"
    )

    # Act
    found = _raw_mkdir_calls(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 2, "both mkdir calls flagged; the ensure_folder call is not"


def test_raw_mkdir_allowlist_has_no_stale_entries():
    """Every allowlisted file still holds a raw mkdir, so the list can't rot."""
    # Arrange / Act
    actual = set(_raw_mkdir_files())

    # Assert
    stale = sorted(ALLOWED_RAW_MKDIR - actual)
    assert stale == [], (
        "ALLOWED_RAW_MKDIR lists files with no raw mkdir left; drop them so the "
        "allowlist stays a description of reality:\n  " + "\n  ".join(stale)
    )


# ── Name patterns have one reader ─────────────────────────────────────────────
#
# A typed name pattern (a list filter, a file glob) is read by
# ``matching.matches``: a wildcard makes an anchored glob, plain text means
# contains, case never matters.  Before it, three modules each called
# ``fnmatch.fnmatch`` with their own wildcard test, and its case follows the
# OS.  Importing ``fnmatch`` anywhere else is a second vocabulary.
MATCHER_OWNER = "termapy/matching.py"


def _fnmatch_imports(tree: ast.AST, rel: str) -> list[str]:
    """Every import of the ``fnmatch`` module in ``rel``."""
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if (isinstance(node, ast.Import) and any(alias.name == "fnmatch" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "fnmatch")
    ]


@pytest.mark.rule("name-one-matcher")
def test_name_patterns_are_read_only_by_the_matcher():
    """Only termapy/matching.py imports fnmatch."""
    # Arrange / Act
    violations = [
        line
        for path in _package_python_files()
        if (rel := path.relative_to(SRC.parent).as_posix()) != MATCHER_OWNER
        for line in _fnmatch_imports(ast.parse(path.read_text(encoding="utf-8")), rel)
    ]

    # Assert
    assert violations == [], (
        "a typed name pattern is read by termapy.matching.matches:\n  "
        + "\n  ".join(violations)
        + "\n\nCall matches(name, pattern) (and is_glob(pattern) where plain"
        "\ntext names one thing) instead of fnmatch."
    )


def test_matcher_guard_fires_on_a_probe():
    """The guard flags both import forms of fnmatch and passes the matcher's own import."""
    # Arrange
    probe = (
        "import fnmatch\nfrom fnmatch import fnmatchcase\nfrom termapy.matching import matches\n"
    )

    # Act
    found = _fnmatch_imports(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 2, "both fnmatch imports flagged; the matcher import is not"


# ── Whether a path stays in a folder has one check ──────────────────────────
#
# ``folders.inside`` (a name under a folder) and ``folders.is_inside`` (a path
# where it stands).  Before them seven modules each resolved, compared and
# caught the Windows OSError their own way, or not at all.  A path that only
# needs shortening for display may call ``relative_to``; asking
# ``is_relative_to`` anywhere else is a second check.
CONTAINMENT_OWNER = "termapy/folders.py"


def _is_relative_to_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every ``.is_relative_to(...)`` call in ``rel``."""
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "is_relative_to"
    ]


@pytest.mark.rule("path-inside-one-check")
def test_containment_is_asked_only_of_folders():
    """Only termapy/folders.py calls is_relative_to."""
    # Arrange / Act
    violations = [
        line
        for path in _package_python_files()
        if (rel := path.relative_to(SRC.parent).as_posix()) != CONTAINMENT_OWNER
        for line in _is_relative_to_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
    ]

    # Assert
    assert violations == [], (
        "whether a path stays in a folder is termapy.folders' question:\n  "
        + "\n  ".join(violations)
        + "\n\nCall inside(folder, name) for a name under a folder, or"
        "\nis_inside(path, folder) for a path where it stands."
    )


@pytest.mark.rule("path-inside-one-check")
def test_containment_guard_fires_on_a_probe():
    """The guard flags a hand-written containment check and passes a call to the owner."""
    # Arrange
    probe = "ok = path.resolve().is_relative_to(root)\ninside(root, name)\nis_inside(path, root)\n"

    # Act
    found = _is_relative_to_calls(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 1, "the hand-written check is flagged; the two owner calls are not"


# ── A text line goes out one way ──────────────────────────────────────────────
#
# A device line goes through ``ctx.serial.send``, which adds the line ending,
# encodes it and paces it (tx_char_delay_ms / tx_line_delay_ms).  Encoding a
# line by hand and writing the bytes skips the pacing: until 2026-10-31 ten
# places did, /term.send among them.  Binary is the other path, ``write``
# with bytes as given.  These files write encoded text on purpose.
ENCODED_WRITE_ALLOWED = {
    # The owner: SerialHandle.send itself.
    "termapy/plugins/handles/serial.py",
    # /raw sends text exactly as typed: no line ending, no pacing.
    "termapy/builtins/commands/raw.py",
    # The protocol subsystem is binary, its setup lines included
    # (decision 2026-10-31): /proto, its debugger, termapy --proto.
    "termapy/builtins/commands/proto.py",
    "termapy/proto_debug.py",
    "termapy/protocol/runner.py",
}


def _is_encode_call(node: ast.AST) -> bool:
    """Whether ``node`` is a call of some ``.encode(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "encode"
    )


def _encoded_writes(tree: ast.AST, rel: str) -> list[str]:
    """Every ``.write(...)`` of encoded text in ``rel``, written directly or through a name.

    A name counts when the same function (or the module) assigns it from an
    ``.encode(...)`` call: ``payload = line.encode(enc); port.write(payload)``.
    """
    scopes: list[ast.AST] = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    scopes.append(tree)
    found: dict[int, str] = {}
    for scope in scopes:
        encoded = {
            target.id
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign) and _is_encode_call(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(scope):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write"
                and node.args
            ):
                continue
            arg = node.args[0]
            if _is_encode_call(arg) or (isinstance(arg, ast.Name) and arg.id in encoded):
                found[id(node)] = f"{rel}:{node.lineno}  {ast.unparse(node)}"
    return sorted(found.values())


def _encoded_write_files() -> dict[str, list[str]]:
    """``{rel: [violation lines]}`` for every package file that writes encoded text.

    Device code under a ``firmware/`` folder runs on the board, not in
    termapy, and is not read.
    """
    found: dict[str, list[str]] = {}
    for path in _package_python_files():
        if "firmware" in path.relative_to(SRC).parts:
            continue
        rel = path.relative_to(SRC.parent).as_posix()
        writes = _encoded_writes(ast.parse(path.read_text(encoding="utf-8")), rel)
        if writes:
            found[rel] = writes
    return found


@pytest.mark.rule("tx-text-one-path")
def test_text_lines_go_out_through_send():
    """Outside the allowlist no code encodes text and writes the bytes; a line goes through ctx.serial.send."""
    # Arrange / Act
    violations = [
        line
        for rel, lines in _encoded_write_files().items()
        if rel not in ENCODED_WRITE_ALLOWED
        for line in lines
    ]

    # Assert
    assert violations == [], (
        "a device line goes through ctx.serial.send, which adds the line ending,"
        " encodes and paces it:\n  "
        + "\n  ".join(violations)
        + "\n\nCall ctx.serial.send(text); write(bytes) is for binary, sent as given."
    )


@pytest.mark.rule("tx-text-one-path")
def test_tx_guard_fires_on_a_probe():
    """The guard flags an encoded write, direct or through a name, and passes a bytes write."""
    # Arrange
    probe = (
        "def direct(ctx, text):\n"
        "    ctx.serial.write((text + '\\r').encode('utf-8'))\n"
        "def named(port, text):\n"
        "    payload = text.encode('ascii')\n"
        "    port.write(payload)\n"
        "def binary(ctx, data):\n"
        "    ctx.serial.write(bytes(data))\n"
    )

    # Act
    found = _encoded_writes(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 2, f"the direct and the named encoded writes, not the bytes one: {found}"


@pytest.mark.rule("tx-text-one-path")
def test_encoded_write_allowlist_has_no_stale_entries():
    """Every ENCODED_WRITE_ALLOWED file still writes encoded text, so the list only describes reality."""
    # Arrange / Act
    actual = set(_encoded_write_files())

    # Assert
    stale = sorted(ENCODED_WRITE_ALLOWED - actual)
    assert stale == [], (
        "ENCODED_WRITE_ALLOWED lists files with no encoded write left; drop them:\n  "
        + "\n  ".join(stale)
    )


# ── Suggestions have one owner ────────────────────────────────────────────────
#
# A name that names nothing is answered by ``matching.closest`` and phrased by
# ``matching.did_you_mean``.  Before them (2026-10-30) four modules had their
# own: a hand-written edit distance in repl.py, a private import of it in the
# config editor, and difflib in the profile loader, worded four ways.  A
# difflib import, an edit-distance function or the phrase anywhere else is a
# fifth copy.  The phrase is allowed where it is not a name suggestion.
SUGGESTION_PHRASE_ALLOWED = {
    # A syntax hint, not a name match: "/var X = 1" is how to set a variable.
    "termapy/builtins/commands/var.py",
    # The editor's help pane draws the suggested key in green; the key
    # itself comes from matching.closest.
    "termapy/dialogs/config_editor.py",
}


def _suggestion_copies(tree: ast.AST, rel: str, *, phrase_allowed: bool = False) -> list[str]:
    """Every difflib import, edit-distance function and did-you-mean string in ``rel``, docstrings aside."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Import) and any(alias.name == "difflib" for alias in node.names)
        ) or (isinstance(node, ast.ImportFrom) and node.module == "difflib"):
            found.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "edit_distance" in node.name
        ):
            found.append(f"{rel}:{node.lineno}  def {node.name}")
        elif (
            not phrase_allowed
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "did you mean" in node.value.casefold()
            and id(node) not in docstrings
        ):
            found.append(f"{rel}:{node.lineno}  {node.value!r}")
    return found


@pytest.mark.rule("did-you-mean-one")
def test_suggestions_have_one_owner():
    """Only matching.py computes or phrases a did-you-mean suggestion."""
    # Arrange / Act
    violations = [
        line
        for path in _package_python_files()
        if (rel := path.relative_to(SRC.parent).as_posix()) != MATCHER_OWNER
        for line in _suggestion_copies(
            ast.parse(path.read_text(encoding="utf-8")),
            rel,
            phrase_allowed=rel in SUGGESTION_PHRASE_ALLOWED,
        )
    ]

    # Assert
    assert violations == [], (
        "a mistyped name's suggestion comes from termapy.matching:\n  "
        + "\n  ".join(violations)
        + "\n\nCall did_you_mean(closest(name, candidates)) instead."
    )


@pytest.mark.rule("did-you-mean-one")
def test_suggestion_guard_fires_on_a_probe():
    """The guard flags difflib, an edit distance and the phrase, never a docstring."""
    # Arrange
    probe = (
        '"""A docstring may say did you mean."""\n'
        "import difflib\n"
        "def _edit_distance(a, b):\n    return 0\n"
        'def fail(name):\n    return f"Unknown: {name} -- did you mean {name}?"\n'
    )

    # Act
    found = _suggestion_copies(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 3, "the import, the function and the phrase; not the docstring"


@pytest.mark.rule("did-you-mean-one")
def test_the_suggestion_allowlist_is_not_stale():
    """Each file allowed the phrase still uses it, so the list only shrinks."""
    stale = [
        rel
        for rel in sorted(SUGGESTION_PHRASE_ALLOWED)
        if "did you mean" not in (SRC.parent / rel).read_text(encoding="utf-8").casefold()
    ]
    assert stale == [], f"drop these from SUGGESTION_PHRASE_ALLOWED: {stale}"


# ── Commands that only show say so ────────────────────────────────────────────
#
# A click on a link runs a command at once only when the command declares
# ``shows=True``: it only shows or opens, so the click cannot act.  Forgetting
# the flag is safe but leaves the click filling the input line instead, and
# the generated folder lists had it while the hand-written ones did not
# (2026-10-30).  Every command named like a listing or a viewer declares it,
# or this table says why it cannot, and every frontend registering a name
# agrees on it.
SHOW_SUFFIXES = frozenset(
    {"list", "info", "dump", "show", "explore", "catalog", "help", "lib", "regs", "loaded"}
)
SHOWS_EXEMPT: dict[str, str] = {
    "app.ver.info": "fetches the newest release from PyPI",
    "cfg.info": "writes the project report to the config's folder",
    "port.info": "on Windows, disconnected, opens the port to test whether it is in use",
}


def _shows_problems(tables: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """``(undeclared, disagreeing)``: listing-named commands without shows, and names the frontends split on."""
    names = set().union(*tables.values())
    undeclared = sorted(
        {
            f"{frontend}: {name}"
            for frontend, table in tables.items()
            for name, info in table.items()
            if info.safety == "safe"  # readonly and up can never declare it
            and name.rsplit(".", 1)[-1] in SHOW_SUFFIXES
            and not info.shows
            and name not in SHOWS_EXEMPT
        }
    )
    disagreeing = sorted(
        name
        for name in names
        if len({table[name].shows for table in tables.values() if name in table}) > 1
    )
    return undeclared, disagreeing


@pytest.mark.rule("shows-declared")
def test_commands_that_only_show_declare_it(tmp_path):
    """Every listing or viewer command declares shows=True, or SHOWS_EXEMPT says why."""
    # Arrange / Act
    undeclared, disagreeing = _shows_problems(_frontend_plugins(tmp_path))

    # Assert
    assert undeclared == [], (
        "these only show or open, so a click on a link to them may run them; declare "
        "shows=True, or add the name to SHOWS_EXEMPT with the reason it acts:\n  "
        + "\n  ".join(undeclared)
    )
    assert disagreeing == [], f"frontends disagree on shows= for: {disagreeing}"


@pytest.mark.rule("shows-declared")
def test_shows_guard_fires_on_a_probe():
    """The guard flags an undeclared listing and a frontend split, and passes an exempt name."""
    from types import SimpleNamespace

    def info(shows: bool) -> SimpleNamespace:
        return SimpleNamespace(shows=shows, safety="safe")

    tables = {
        "CLI": {"thing.list": info(False), "x.info": info(True), "app.ver.info": info(False)},
        "TUI": {"x.info": info(False)},
    }
    undeclared, disagreeing = _shows_problems(tables)
    assert undeclared == ["CLI: thing.list", "TUI: x.info"], "both missing declarations"
    assert disagreeing == ["x.info"], "the frontends split on x.info"


@pytest.mark.rule("shows-declared")
def test_shows_exemptions_are_not_stale(tmp_path):
    """Each exempt name is one the guard would flag (registered, safe, undeclared), so the list only shrinks."""
    tables = _frontend_plugins(tmp_path)
    stale = sorted(
        name
        for name in SHOWS_EXEMPT
        if not any(
            name in table and not table[name].shows and table[name].safety == "safe"
            for table in tables.values()
        )
    )
    assert stale == [], f"drop these from SHOWS_EXEMPT: {stale}"


# ── Guard: one answer to "is this value OK" (registry cfg-schema-one-table) ──
#
# A load, the config editor and /cfg each ask config.check_cfg_value.  Before
# 2026-11-02 each kept its own rules and they disagreed: the editor's baud list
# lacked four rates a load accepted, and /cfg checked almost nothing.
CFG_CHECK_SURFACES = {
    "config.py": "validate_config",
    "dialogs/config_editor.py": "ConfigEditor._validate_value",
    "builtins/commands/cfg.py": "_typed_value",
}


def _calls_the_check(tree: ast.Module, qualname: str) -> bool:
    """Whether ``qualname`` in ``tree`` calls ``check_cfg_value``."""
    return any(
        scope == qualname
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "check_cfg_value"
        for scope, node in _scoped_nodes(tree)
    )


@pytest.mark.rule("cfg-schema-one-table")
def test_every_surface_checks_through_the_one_validator():
    """A load, the config editor and /cfg all ask config.check_cfg_value."""
    # Arrange
    trees = {rel.replace("\\", "/"): tree for rel, tree in _source_modules()}

    # Act
    missing = [
        f"{rel}: {qualname}"
        for rel, qualname in CFG_CHECK_SURFACES.items()
        if not _calls_the_check(trees[rel], qualname)
    ]

    # Assert
    assert missing == [], f"these stopped asking the one validator: {missing}"


@pytest.mark.rule("cfg-schema-one-table")
def test_one_validator_guard_fires_on_a_probe():
    """The guard passes a function that calls the check and flags one that does its own."""
    good = ast.parse("def surface(cfg):\n    return check_cfg_value('k', 1, cfg)\n")
    bad = ast.parse("def surface(cfg):\n    return cfg['k'] > 0\n")
    assert _calls_the_check(good, "surface"), "it asks the validator"
    assert not _calls_the_check(bad, "surface"), "a rule of its own is flagged"


# ── Guard: a family says what it is for (registry family-has-intro) ─────────
#
# A command with subcommands, at any level, declares Command.intro.  Before
# 2026-11-02 bare /dev printed a list with nothing saying what the family was for.
INTRO_MAX_CHARS = 220


def _intro_problems(tables: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """``(missing, unfit)``: families with no intro, and intros too long or not one line."""
    missing: set[str] = set()
    unfit: set[str] = set()
    for frontend, table in tables.items():
        for name, info in table.items():
            kids = [kid for kid in info.children if kid in table]
            if not kids:
                continue
            if not info.intro:
                missing.add(f"{frontend}: {name}")
            if len(info.intro) > INTRO_MAX_CHARS or "\n" in info.intro:
                unfit.add(name)
    return sorted(missing), sorted(unfit)


@pytest.mark.rule("family-has-intro")
def test_every_family_says_what_it_is_for(tmp_path):
    """Every command with subcommands, at any level, declares a short intro."""
    # Arrange / Act
    missing, unfit = _intro_problems(_frontend_plugins(tmp_path))

    # Assert
    assert missing == [], f"these families do not say what they are for: {missing}"
    assert unfit == [], f"an intro is one line of at most {INTRO_MAX_CHARS} characters: {unfit}"


@pytest.mark.rule("family-has-intro")
def test_family_intro_guard_fires_on_a_probe():
    """The guard flags a silent command with subcommands at any level and an overlong intro, and passes a leaf."""
    from types import SimpleNamespace

    def info(children=(), intro="") -> SimpleNamespace:
        return SimpleNamespace(children=list(children), intro=intro)

    table = {
        "a": info(["a.x"]),
        "a.x": info(["a.x.y"]),
        "a.x.y": info(),
        "b": info(["b.z"], intro="x" * 500),
        "b.z": info(["b.z.y"], intro="Fine."),
        "b.z.y": info(),
        "leaf": info(),
    }
    missing, unfit = _intro_problems({"CLI": table})
    assert missing == ["CLI: a", "CLI: a.x"], "silent at the top and lower down"
    assert unfit == ["b"], "an overlong intro"


# ── Guard: a state read changes nothing (registry state-read-immutable) ─────
#
# ``Command.state`` promises that a command's bare form changes nothing,
# sends no bytes, opens no port, writes no file and answers, because /help
# runs it for every reader of its page.  This reads each tagged command
# twice against a connected DEMO and compares what the session holds.  An
# empty answer is one only these two may give: nothing is loaded.
STATE_MAY_ANSWER_EMPTY = frozenset({"sym.info", "profile.info"})


def _session_state(cli: Any) -> tuple[str, str, list[tuple[str, int, int]]]:
    """The config, every non-empty namespace and the config folder's files, comparable."""
    folder = Path(cli.config_path).parent
    namespaces = {key: value for key, value in cli.repl.ctx._namespaces.items() if value}
    return (
        json.dumps(cli.repl._cfg_data, default=str, sort_keys=True),
        repr(namespaces),
        sorted(
            (str(path), path.stat().st_mtime_ns, path.stat().st_size)
            for path in folder.rglob("*")
            if path.is_file()
        ),
    )


def _state_read_problems(cli: Any, capsys: Any, names: Iterable[str]) -> list[str]:
    """What reading each command's state did that it promised not to."""
    ctx = cli.repl.ctx
    sent: list[bytes] = []
    saved_write = ctx.serial.write
    ctx.serial.write = sent.append  # the seam dispatch_full hands a writer in through
    problems: list[str] = []
    try:
        for name in names:
            capsys.readouterr()
            before = _session_state(cli)
            first = cli.repl.read_state(name)
            second = cli.repl.read_state(name)
            printed = capsys.readouterr().out
            found = {
                "changed the session": _session_state(cli) != before,
                "sent bytes": bool(sent),
                f"printed {printed!r}": bool(printed),
                "answered differently the second time": first.value != second.value,
                "gave no answer": (
                    first.success
                    and first.view is None
                    and first.value in ("", None)
                    and name not in STATE_MAY_ANSWER_EMPTY
                ),
            }
            problems += [f"{name} {what}" for what, happened in found.items() if happened]
            sent.clear()
    finally:
        ctx.serial.write = saved_write
    return problems


def _connected_demo(tmp_path: Path) -> Any:
    """A CLI host on a connected DEMO over a throwaway config, started as run() starts it."""
    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO"}}
    path = tmp_path / "state" / "state.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    cli = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    cli.repl.global_root = tmp_path
    cli._fire_app_start()
    cli._connect()
    return cli


@pytest.mark.rule("state-read-immutable")
def test_reading_a_commands_state_changes_nothing(tmp_path, capsys):
    """Every tagged command reads twice with the session, the files and the wire unchanged, printing nothing."""
    # Arrange
    cli = _connected_demo(tmp_path)
    names = sorted(name for name, info in cli.repl._plugins.items() if info.state)
    assert len(names) > 50, "the tag reached the table"

    # Act
    try:
        problems = _state_read_problems(cli, capsys, names)
    finally:
        cli._disconnect()

    # Assert
    assert problems == [], (
        "/help runs these for every reader of their page, so a read must change nothing:\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.rule("state-read-immutable")
def test_state_read_guard_fires_on_a_probe(tmp_path, capsys):
    """A bare form that sets a flag, writes a file, sends bytes, prints or answers nothing is named."""
    # Arrange
    from termapy.plugins import CmdResult

    cli = _connected_demo(tmp_path)
    folder = Path(cli.config_path).parent

    def sets_a_flag(ctx, args):
        """Change the session."""
        ctx.ns("flags")["probe"] = True
        return CmdResult.ok(value="on")

    def writes_a_file(ctx, args):
        """Touch the config folder."""
        (folder / "probe.txt").write_text("x")
        return CmdResult.ok(value="on")

    def sends_bytes(ctx, args):
        """Talk to the device."""
        ctx.serial.send("AT")
        return CmdResult.ok(value="on")

    def prints(ctx, args):
        """Print past the silent level."""
        print("probe")
        return CmdResult.ok(value="on")

    def answers_nothing(ctx, args):
        """Print prose and return no answer."""
        ctx.io.output("prose only")
        return CmdResult.ok(value="")

    def is_clean(ctx, args):
        """Keep the promise."""
        return CmdResult.ok(value="on")

    handlers = [sets_a_flag, writes_a_file, sends_bytes, prints, answers_nothing, is_clean]
    for handler in handlers:
        cli.repl.register_hook(handler.__name__, "", "h", handler, state=True)

    # Act
    try:
        problems = _state_read_problems(cli, capsys, [handler.__name__ for handler in handlers])
    finally:
        cli._disconnect()

    # Assert
    named = {problem.split(" ", 1)[0] for problem in problems}
    expected = {"sets_a_flag", "writes_a_file", "sends_bytes", "prints", "answers_nothing"}
    assert named == expected, f"each broken promise is named and the clean one is not: {problems}"


# ── Guard: a command that reports state says so (registry state-declared) ───
#
# The shapes that promise a bare query: a setting's synopsis (``{on|off|toggle}``
# or ``{...|cycle}``), an ``.info`` page, and a ``cfg=`` phrase saying "bare
# shows it".  Each declares ``state=True`` or says here why /help must not run
# it.  The list only shrinks.
STATE_EXEMPT: dict[str, str] = {
    "app.ver.info": "fetches the newest release from PyPI",
    "cfg.info": "writes the project report to the config's folder",
    "edit.info": "opens the project report in an editor",
    "mcp.info": "walks every command's long_help for the catalog",
    "mem.info": "sends MEM.INFO to the device and rewrites the cache",
    "port.info": "on Windows, disconnected, opens the port to test whether it is in use",
}


def _reports_state(name: str, info: Any) -> bool:
    """Whether a declaration promises that the bare form is a query; a required argument means it has none."""
    from termapy.plugins.command import has_required_arg

    if has_required_arg(info.args):
        return False
    return (
        info.args.endswith(("toggle}", "cycle}"))
        or name.rsplit(".", 1)[-1] == "info"
        or any("bare shows it" in use for use in info.cfg.values())
    )


def _state_declaration_problems(
    tables: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """``(undeclared, disagreeing)``: state-shaped commands without the tag, and names the frontends split on."""
    undeclared = sorted(
        {
            f"{frontend}: {name}"
            for frontend, table in tables.items()
            for name, info in table.items()
            if _reports_state(name, info) and not info.state and name not in STATE_EXEMPT
        }
    )
    names = set().union(*tables.values())
    disagreeing = sorted(
        name
        for name in names
        if len({table[name].state for table in tables.values() if name in table}) > 1
    )
    return undeclared, disagreeing


@pytest.mark.rule("state-declared")
def test_commands_that_report_state_declare_it(tmp_path):
    """Every setting and .info page declares state=True, or STATE_EXEMPT says why /help must not run it."""
    # Arrange / Act
    undeclared, disagreeing = _state_declaration_problems(_frontend_plugins(tmp_path))

    # Assert
    assert undeclared == [], (
        "these promise a bare query, so /help can show their state; declare state=True, or "
        "add the name to STATE_EXEMPT with the reason a help page must not run it:\n  "
        + "\n  ".join(undeclared)
    )
    assert disagreeing == [], f"frontends disagree on state= for: {disagreeing}"


@pytest.mark.rule("state-declared")
def test_state_declaration_guard_fires_on_a_probe():
    """The guard flags an undeclared setting, .info page and cfg phrase, a frontend split, and passes an exempt name."""
    from types import SimpleNamespace

    def info(state: bool, args: str = "", cfg: dict[str, str] | None = None) -> SimpleNamespace:
        return SimpleNamespace(state=state, args=args, cfg=cfg or {})

    tables = {
        "CLI": {
            "a.flag": info(False, args="{on|off|toggle}"),
            "a.mode": info(False, args="{x|y|cycle}"),
            "a.info": info(False),
            "a.size": info(False, cfg={"max_lines": "set for the session; bare shows it"}),
            "a.send": info(False, args="<text>"),
            "a.crc.info": info(False, args="<name>"),
            "cfg.info": info(False),
            "b.flag": info(True, args="{on|off|toggle}"),
        },
        "TUI": {"b.flag": info(False)},
    }
    undeclared, disagreeing = _state_declaration_problems(tables)
    expected = ["CLI: a.flag", "CLI: a.info", "CLI: a.mode", "CLI: a.size"]
    assert undeclared == expected, "each shape is caught; acting and exempt commands are not"
    assert disagreeing == ["b.flag"], "the frontends split on b.flag"


@pytest.mark.rule("state-declared")
def test_state_exemptions_are_not_stale(tmp_path):
    """Each exempt name is one the guard would flag (registered, state-shaped, undeclared), so the list only shrinks."""
    tables = _frontend_plugins(tmp_path)
    stale = sorted(
        name
        for name in STATE_EXEMPT
        if not any(
            name in table and not table[name].state and _reports_state(name, table[name])
            for table in tables.values()
        )
    )
    assert stale == [], f"drop these from STATE_EXEMPT: {stale}"


# ── Port names are compared one way ───────────────────────────────────────────
#
# A typed port name and a listed one are the same port when the OS would open
# the same device: Windows ignores case and the ``\\.\`` prefix, Linux and
# macOS follow symlinks.  ``port_control.same_port`` is that rule; ``==``
# between two device names is how a config's ``com3`` connected but read as
# unplugged everywhere else (2026-10-29).

PORT_NAME_MODULES = (
    "termapy/port_control.py",
    "termapy/cli_flags.py",
    "termapy/serial_engine.py",
    "termapy/title_bar.py",
    "termapy/port_format.py",
    "termapy/pickers.py",
    "termapy/info_views.py",
    "termapy/dialogs/port_picker.py",
    "termapy/dialogs/quick_setup.py",
)
_DEVICE_NAMES = frozenset({"device", "port_name", "connected_port", "current_port", "open_device"})


def _device_name_compares(tree: ast.AST, rel: str) -> list[str]:
    """``==`` / ``!=`` between expressions named like a device (not against a literal)."""

    def name_of(expr: ast.expr) -> str | None:
        if isinstance(expr, ast.Attribute):
            return expr.attr
        if isinstance(expr, ast.Name):
            return expr.id
        return None

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        if any(isinstance(operand, ast.Constant) for operand in operands):
            continue
        if any(name_of(operand) in _DEVICE_NAMES for operand in operands):
            found.append(f"{rel}:{node.lineno}")
    return found


@pytest.mark.rule("port-name-one-compare")
def test_port_names_are_compared_only_by_same_port():
    """No port module compares two device names with == or !=."""
    # Arrange / Act
    violations = [
        line
        for rel in PORT_NAME_MODULES
        for line in _device_name_compares(
            ast.parse((SRC.parent / rel).read_text(encoding="utf-8")), rel
        )
    ]

    # Assert
    assert violations == [], (
        "compare port names with port_control.same_port, which matches them as the OS opens "
        "the port:\n  " + "\n  ".join(violations)
    )


@pytest.mark.rule("port-name-one-compare")
def test_port_name_compare_guard_fires_on_a_probe():
    """The guard flags == and != between device names and passes same_port and literals."""
    # Arrange
    probe = (
        "if fact.device == candidate: pass\n"
        "if port_name != device: pass\n"
        "if same_port(fact.device, candidate): pass\n"
        "if device == '': pass\n"
    )

    # Act
    found = _device_name_compares(ast.parse(probe), "probe")

    # Assert
    assert found == ["probe:1", "probe:2"], (
        "both direct compares flagged; same_port and a literal are not"
    )


# ── The symbols namespace has one writer ──────────────────────────────────────
#
# ``ctx.ns("symbols")`` holds the build table, the device list and the merged
# view, and ``symbols/session.py`` is the only module that touches it: its
# installers rebuild the merged view from the two inputs, so nothing else can
# leave the three keys disagreeing (the bug that motivated the split was an
# import dropping the device registers).  Readers go through ``get_table`` /
# ``get_devices`` / ``get_build_table``; a direct ``ctx.ns("symbols")``, even
# a read, is a second opinion about the namespace's shape.
SYMBOLS_NS_OWNER = "termapy/symbols/session.py"


def _symbols_ns_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every ``<expr>.ns("symbols")`` / ``.ns(SYMBOLS_NS)`` call in ``rel``."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "ns"
            and len(node.args) == 1
        ):
            continue
        arg = node.args[0]
        names_symbols = (
            (isinstance(arg, ast.Constant) and arg.value == "symbols")
            or (isinstance(arg, ast.Name) and arg.id == "SYMBOLS_NS")
            or (isinstance(arg, ast.Attribute) and arg.attr == "SYMBOLS_NS")
        )
        if names_symbols:
            found.append(f"{rel}:{node.lineno}  {ast.unparse(node)}")
    return found


def _symbols_ns_files() -> dict[str, list[str]]:
    """``{rel: [call lines]}`` for every package file that touches the namespace."""
    found: dict[str, list[str]] = {}
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        calls = _symbols_ns_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
        if calls:
            found[rel] = calls
    return found


@pytest.mark.rule("symbols-one-owner")
def test_symbols_namespace_is_touched_only_by_its_session_module():
    """No code outside ``symbols/session.py`` calls ``ctx.ns("symbols")``."""
    # Arrange / Act
    violations = [
        line
        for rel, calls in _symbols_ns_files().items()
        if rel != SYMBOLS_NS_OWNER
        for line in calls
    ]

    # Assert
    assert violations == [], (
        "the symbols namespace has one writer, symbols/session.py:\n  "
        + "\n  ".join(violations)
        + "\n\nRead through get_table / get_devices / get_build_table; write through"
        "\ninstall_build / install_devices."
    )


def test_symbols_namespace_guard_fires_on_a_probe():
    """The guard detects the literal, the constant, and a qualified constant."""
    # Arrange
    probe = (
        'ctx.ns("symbols")["table"] = table\n'
        "ns = ctx.ns(SYMBOLS_NS)\n"
        "self.ctx.ns(session.SYMBOLS_NS).clear()\n"
        'ctx.ns("memory").clear()\n'
        "get_table(ctx)\n"
    )

    # Act
    found = _symbols_ns_calls(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 3, (
        "the three symbols-namespace calls flagged; the memory namespace and the getter are not"
    )


def test_symbols_namespace_owner_still_touches_it():
    """The exemption describes reality: session.py is where the namespace is written."""
    # Arrange / Act
    actual = _symbols_ns_files()

    # Assert
    assert SYMBOLS_NS_OWNER in actual, (
        "session.py no longer touches ctx.ns('symbols'); move the exemption to the new owner"
    )


# ── The app start is announced in one place ──────────────────────────────────

# TerminalHost._fire_app_start: the frontend's pre-start work, then
# on_app_prestart, then on_app_start.  A frontend firing on_app_start itself
# would skip pre-start, and the startup load report would miss the log.
_APP_START_EVENTS: frozenset[str] = frozenset({"on_app_prestart", "on_app_start"})
_APP_START_OWNER = "termapy/terminal_host.py"


def _app_start_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every ``fire_lifecycle("on_app_prestart" | "on_app_start")`` call in ``rel``."""
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fire_lifecycle"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in _APP_START_EVENTS
    ]


@pytest.mark.rule("prestart-before-start")
def test_app_start_is_announced_only_by_the_host():
    """Only TerminalHost fires the app-start events, so pre-start always comes first."""
    # Arrange / Act
    violations = [
        line
        for path in _package_python_files()
        if (rel := path.relative_to(SRC.parent).as_posix()) != _APP_START_OWNER
        for line in _app_start_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
    ]

    # Assert
    assert violations == [], (
        "the app start is announced by TerminalHost._fire_app_start alone:\n  "
        + "\n  ".join(violations)
        + "\n\nCall self._fire_app_start() (the host's pre-start work, then"
        "\non_app_prestart, then on_app_start) instead of firing either event."
    )


@pytest.mark.rule("prestart-before-start")
def test_app_start_guard_fires_on_a_probe():
    """The guard flags both app-start events, aliased receiver or not, and no other event."""
    # Arrange
    probe = (
        'self.repl.fire_lifecycle("on_app_start")\n'
        'host.repl.fire_lifecycle("on_app_prestart")\n'
        'self.repl.fire_lifecycle("on_connect")\n'
    )

    # Act
    found = _app_start_calls(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 2, "both app-start calls flagged; on_connect is not"


# ── Test headers ─────────────────────────────────────────────────────────────

TESTS = Path(__file__).parent

# Test files that still hold a test with no docstring header, written before
# the rule existed.  The list only shrinks: files come off as batches convert
# them (the stale-entry test below forces that), and when it is empty this
# constant and its stale-entry test are deleted.  Never add a file here -- a
# new or edited test gets its header instead.
ALLOWED_HEADERLESS: frozenset[str] = frozenset(
    {
        "test_devices.py",
        "test_devices_svd.py",
        "test_memory_views.py",
        "test_sym_commands.py",
    }
)


def _test_functions(
    body: list[ast.stmt],
    prefix: str = "",
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """``(qualname, def)`` for every test pytest collects from ``body``.

    pytest's defaults, which pyproject does not override: ``test*`` functions
    at module level and ``test*`` methods of ``Test*`` classes, nested
    classes included.  Helpers, fixtures and non-``Test`` classes are not
    tests, so they need no header.
    """
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in body:
        is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        if is_function and node.name.startswith("test"):
            found.append((prefix + node.name, node))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            found.extend(_test_functions(node.body, f"{prefix}{node.name}."))
    return found


def _headerless_tests(tree: ast.Module, rel: str) -> list[str]:
    """Every test in ``rel`` with no docstring header."""
    return [
        f"{rel}:{func.lineno}  {qualname}"
        for qualname, func in _test_functions(tree.body)
        if not ast.get_docstring(func)
    ]


def _headerless_files() -> dict[str, list[str]]:
    """``{rel: [violation lines]}`` for every test file with a headerless test."""
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        rel = path.relative_to(TESTS).as_posix()
        missing = _headerless_tests(ast.parse(path.read_text(encoding="utf-8")), rel)
        if missing:
            found[rel] = missing
    return found


@pytest.mark.rule("test-headers")
def test_every_test_has_a_header():
    """Every test outside the shrinking allowlist says what it pins in a docstring.

    The name says what a test does; the header says why it matters, which
    is what a reader has lost when they come back to it later.  See the
    Testing section of CLAUDE.md.
    """
    # Arrange / Act
    violations = [
        line
        for rel, missing in _headerless_files().items()
        if rel not in ALLOWED_HEADERLESS
        for line in missing
    ]

    # Assert
    assert violations == [], (
        "every test opens with a one-line docstring header saying what it pins:\n  "
        + "\n  ".join(violations)
    )


def test_header_guard_fires_on_a_probe():
    """The guard flags exactly the tests pytest collects, and only headerless ones."""
    # Arrange -- two headerless tests, plus a headed test, a helper, a
    # fixture and a non-Test class that must all pass
    probe = (
        "def test_bare():\n"
        "    assert True\n"
        "def test_headed():\n"
        '    """Has a header."""\n'
        "def _helper():\n"
        "    pass\n"
        "@pytest.fixture\n"
        "def board():\n"
        "    pass\n"
        "class TestThing:\n"
        "    def test_method(self):\n"
        "        pass\n"
        "    def helper(self):\n"
        "        pass\n"
        "class Helper:\n"
        "    def test_not_collected(self):\n"
        "        pass\n"
    )

    # Act
    found = _headerless_tests(ast.parse(probe), "probe")

    # Assert
    assert found == ["probe:1  test_bare", "probe:11  TestThing.test_method"], (
        "the module test and the Test-class method flagged; nothing pytest skips"
    )


def test_headerless_allowlist_has_no_stale_entries():
    """Every allowlisted file still has a headerless test, so the list only shrinks."""
    # Arrange / Act
    actual = set(_headerless_files())

    # Assert
    stale = sorted(ALLOWED_HEADERLESS - actual)
    assert stale == [], (
        "ALLOWED_HEADERLESS lists files whose tests all have headers now; drop them:\n  "
        + "\n  ".join(stale)
    )


# ── The rule registry ────────────────────────────────────────────────────────
#
# docs/rules.md is the one list of the rules this file and the rest of the
# suite enforce, each row a stable id, a kind (guarantee = a named test, or
# assumption = review only) and the test.  A test says which rule it pins with
# ``@pytest.mark.rule("<id>")`` (docs/traceability.md); tests/traceability.py
# reads the markers from the source and scripts/sync_traceability.py renders
# them into docs/traceability-matrix.md.  The guards below keep the table and the
# tests agreeing both ways: a row's tests exist and carry the row's marker, a
# guard in this file carries a registered id, and every marker names an id one
# of the sources holds -- so a renamed test cannot leave a row pointing at
# nothing, a new guard cannot ship unlisted, and a tag cannot name a rule
# nobody wrote down.

REGISTRY = SRC.parent.parent / "docs" / "rules.md"

# A guard's companions prove it fires or keep its allowlist honest; they carry
# no id of their own, so the reverse check skips them by name.
_COMPANION_NAME_PARTS = ("fires_on_a_probe", "has_no_stale_entries", "still_touches_it")


def _guard_markers(tree: ast.Module) -> list[tuple[str, set[str]]]:
    """``(name, rule ids in effect)`` of every guard in a module, companions excluded."""
    index = MarkIndex()
    index_module(tree, "guards", index)
    return [
        (name, ids)
        for name, ids in index.functions.items()
        if not any(part in name for part in _COMPANION_NAME_PARTS)
    ]


def _registry_mismatches(
    rows: list[dict[str, str]],
    index: MarkIndex,
    guards: list[tuple[str, set[str]]],
) -> list[str]:
    """Every way the registry and the tests can disagree, as one line each."""
    problems: list[str] = []
    ids: set[str] = set()
    for row in rows:
        rid = row.get("id", "")
        if not RULE_ID.match(rid):
            problems.append(f"registry: bad id {rid!r}")
        elif rid in ids:
            problems.append(f"registry: duplicate id {rid}")
        ids.add(rid)
        kind = row.get("kind", "")
        if kind not in ("guarantee", "assumption"):
            problems.append(f"registry {rid}: kind must be guarantee or assumption, not {kind!r}")
        refs = TEST_REF.findall(row.get("enforced by", ""))
        if kind == "guarantee" and not refs:
            problems.append(f"registry {rid}: a guarantee names the test that enforces it")
        for ref in refs:
            if ref.endswith(".py"):
                known = index.files
            elif ref.startswith("Test"):
                known = index.classes
            else:
                known = index.functions
            if ref not in known:
                problems.append(f"registry {rid}: no such test {ref}")
            elif rid not in known[ref]:
                problems.append(f"registry {rid}: {ref} does not carry @pytest.mark.rule({rid!r})")
    for name, marked in guards:
        if not marked & ids:
            problems.append(f"guard {name}: carries no registered rule marker")
    return problems


@pytest.mark.rule("rules-registry")
def test_rules_registry_matches_the_guards():
    """docs/rules.md and the tests agree both ways.

    A row's tests exist and carry the row's marker, so a guarantee cannot name
    a test that was renamed away or one that does not say it pins the rule;
    every guard here carries a registered id, so a new guard cannot ship
    unlisted.  The registry's intro says how to add a row.
    """
    # Arrange / Act
    rows = registry_rows(REGISTRY.read_text(encoding="utf-8"))
    index = index_tests()
    guards = _guard_markers(ast.parse(Path(__file__).read_text(encoding="utf-8")))
    problems = _registry_mismatches(rows, index, guards)

    # Assert
    assert problems == [], "\n  ".join(["registry and tests disagree:", *problems])


def test_registry_guard_fires_on_a_probe():
    """The guard detects a bad id, a bad kind, a missing test, a bare guarantee, an unmarked test and an unmarked guard."""
    # Arrange -- one good row and one of each defect, plus one marked and
    # one unmarked guard
    rows = [
        {"id": "good-id", "kind": "guarantee", "enforced by": "test_real, TestReal, test_file.py"},
        {"id": "BadId", "kind": "assumption", "enforced by": "review"},
        {"id": "no-kind", "kind": "rule", "enforced by": "review"},
        {"id": "gone-test", "kind": "guarantee", "enforced by": "test_renamed_away"},
        {"id": "bare-one", "kind": "guarantee", "enforced by": "review"},
        {"id": "un-marked", "kind": "assumption", "enforced by": "review; test_real"},
    ]
    index = MarkIndex(
        functions={"test_real": {"good-id"}},
        classes={"TestReal": {"good-id"}},
        files={"test_file.py": {"good-id"}},
    )
    guards = [("test_marked", {"good-id"}), ("test_unmarked", set())]

    # Act
    problems = _registry_mismatches(rows, index, guards)

    # Assert
    assert problems == [
        "registry: bad id 'BadId'",
        "registry no-kind: kind must be guarantee or assumption, not 'rule'",
        "registry gone-test: no such test test_renamed_away",
        "registry bare-one: a guarantee names the test that enforces it",
        "registry un-marked: test_real does not carry @pytest.mark.rule('un-marked')",
        "guard test_unmarked: carries no registered rule marker",
    ], "one line per defect, nothing for the good row or the marked guard"


@pytest.mark.rule("rules-registry")
def test_rule_markers_name_known_ids():
    """Every ``rule`` marker in the suite names one id that an id source holds.

    The same check conftest runs at collection, read from the source here so
    the suite reports it as a test with each offender named, and so a marker
    on a test that is deselected or skipped is checked all the same.
    """
    # Arrange / Act
    index = index_tests()
    problems = index.problems + marker_problems(index.markers, known_ids())

    # Assert
    assert problems == [], "\n  ".join(["rule markers that name nothing:", *problems])


# ── Every command has a test ─────────────────────────────────────────────────
#
# 207 commands were cheap to write and expensive to verify; by 2026-10-12 a
# documented option (/cap.poll labels=) had been untypeable for three months
# because no test and no user touched it.  This guard makes "a new command
# ships with a test" mechanical: every registered command must be named by at
# least one test file.  Commands are grouped by their handler's code object,
# so a generated family (the /proto.crc.<lang> commands, the folder
# subcommands, the /port.chip.* fields) counts once -- one of its names in a
# test exercises the one implementation.  The set below is what was untested
# when the guard was written; it only shrinks.

KNOWN_UNTESTED_COMMANDS: frozenset[str] = frozenset(
    {
        "app.state.edit",
        "cap.hex",
        "edit.plugin.list",
        "edit.proto.list",
        "edit.run.list",
        "help.target",
        "term.encoding",
        "term.log",
        "term.send_bare_enter",
        "xfer.ymodem.send",
    }
)


def _command_groups() -> list[list[str]]:
    """Every registered built-in command name, grouped by handler code object."""
    from termapy.plugins import builtins_dir, load_plugins_from_dir

    groups: dict[object, list[str]] = {}
    for info in load_plugins_from_dir(builtins_dir(), source="built-in").plugins:
        key = getattr(info.handler, "__code__", None) or id(info.handler)
        groups.setdefault(key, []).append(info.name)
    return [sorted(names) for names in groups.values()]


def _tests_text() -> str:
    """Every test file's source, joined, for the name search.

    This file is skipped: its allowlist names every untested command and
    would otherwise count as the test that mentions them.
    """
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in TESTS.rglob("test_*.py")
        if path != Path(__file__)
    )


def _mentioned(name: str, tests_text: str) -> bool:
    """True when ``name`` appears in the tests as a whole dotted token."""
    return re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w.])", tests_text) is not None


def _untested_groups(groups: list[list[str]], tests_text: str) -> list[list[str]]:
    """The groups none of whose names any test file mentions."""
    return [names for names in groups if not any(_mentioned(name, tests_text) for name in names)]


@pytest.mark.rule("command-has-test")
def test_every_command_is_mentioned_by_a_test():
    """Every registered command is named by a test, except the shrinking known-untested set.

    A family generated from one handler counts once.  A new command fails
    this until a test names it; the fix is the test, never a new entry in
    the allowlist.
    """
    # Arrange / Act
    untested = _untested_groups(_command_groups(), _tests_text())
    violations = [
        " ".join(names)
        for names in untested
        if not any(name in KNOWN_UNTESTED_COMMANDS for name in names)
    ]

    # Assert
    assert violations == [], (
        "a registered command has no test that names it; write the test:\n  "
        + "\n  ".join(violations)
    )


def test_command_guard_fires_on_a_probe():
    """The guard flags a group none of whose names appear, and passes a group one of whose names does."""
    # Arrange -- a dotted name, a name that is only a prefix of what the
    # tests mention, and a family with one mentioned member
    groups = [["cap.text"], ["port.open"], ["proto.crc.go", "proto.crc.rust"]]
    tests_text = (
        'eng.dispatch("cap.text t.txt")\nresult = run("/port.open_wide")\nuse("/proto.crc.rust")\n'
    )

    # Act
    untested = _untested_groups(groups, tests_text)

    # Assert
    assert untested == [["port.open"]], (
        "a prefix of a longer token is not a mention; one member covers a family"
    )


def test_known_untested_commands_has_no_stale_entries():
    """Every allowlisted command is still untested, so the set only shrinks."""
    # Arrange / Act
    still_untested = {
        name for names in _untested_groups(_command_groups(), _tests_text()) for name in names
    }

    # Assert
    stale = sorted(KNOWN_UNTESTED_COMMANDS - still_untested)
    assert stale == [], (
        "KNOWN_UNTESTED_COMMANDS lists commands a test now names; drop them:\n  "
        + "\n  ".join(stale)
    )


# ── Every command is in the CLI gold run, or says why not ────────────────────
#
# 2026-10-17: the gold run (tests/cli_gold/cli_test.run) reached 53 of 182
# command families, and a day of behavior changes went in without a gold line
# until Chuck asked.  The gold run is the one end-to-end check of what a user
# reads, so every family is in it -- one member suffices, as for
# command-has-test -- or is listed below with the reason it cannot be.  A new
# command fails here until it gets a gold line or a reason; the list only
# shrinks.

GOLD_RUN = Path(__file__).parent / "cli_gold" / "cli_test.run"

_OPENS_APP = "opens a desktop app or editor; whether it can depends on the machine's desktop"
_HARDWARE = "needs real serial hardware or the network"
_PATH = "prints an absolute path (the run's temp config folder or the user's app folder)"
_DIAG_RUN = "prints this run's diagnostic log or its folder: timestamps, a pid and paths"
_TIMED = "its output carries timing that changes from run to run"
_CRCGLOT = "its output is crcglot's catalogue or generated code, which changes with crcglot"

# One name per family: the family's alphabetically first name.
NOT_IN_GOLD: dict[str, str] = {
    "app.config": _PATH,
    "app.config.dump": "prints the user's app-wide config, which differs per machine",
    "app.config.edit": _OPENS_APP,
    "app.explore": _OPENS_APP,
    "app.kill": "ends other termapy processes on this machine, after a yes; tests/test_breadcrumbs.py drives it",
    "app.list": "lists the user's app folder, which differs per machine",
    "app.alive": "lists the termapy processes on this machine, by PID and start time",
    "app.state": _PATH,
    "app.state.dump": "prints the user's app state (update-check times), which differs per machine",
    "app.state.edit": _OPENS_APP,
    "app.ver": "prints the version, which changes every release",
    "app.ver.info": "prints the version and how this machine installed termapy",
    "app.ver.latest": _HARDWARE,
    "board.list": "lists which dev boards are plugged in, which differs per machine",
    "cap.bin": "a capture runs on the device's timing; tests/test_capture*.py drive it",
    "cap.explore": _OPENS_APP,
    "cap.hex": "a capture runs on the device's timing; tests/test_capture*.py drive it",
    "cap.show": _OPENS_APP,
    "cap.struct": "a capture runs on the device's timing; tests/test_capture*.py drive it",
    "cap.text": "a capture runs on the device's timing; tests/test_capture*.py drive it",
    "cap.wire": _TIMED,
    "cfg.edit": _OPENS_APP,
    "cfg.explore": _OPENS_APP,
    "cfg.help": "prints the same page as /help cfg, which the help section already pins",
    "cfg.icon": "creates a desktop launcher on this machine",
    "cfg.icon.list": "lists the desktop launchers on this machine",
    "cfg.icon.remove": "removes a desktop launcher on this machine",
    "cfg.info": _PATH,
    "cfg.list": _PATH,
    "cfg.show": _OPENS_APP,
    "cls": "clears the screen with an escape sequence that swallows the next line",
    "confirm": "waits for a human to answer",
    "credits": "a long docs page; tests/test_credits_*.py guard its content",
    "demo": "reinstalls the demo config mid-run",
    "demo.force": "reinstalls the demo config mid-run",
    "edit": _OPENS_APP,
    "edit.info": _OPENS_APP,
    "edit.log": _OPENS_APP,
    "edit.plugin": _OPENS_APP,
    "edit.plugin.explore": _OPENS_APP,
    "env.reload": "reads the machine's environment variables",
    "exit": "ends the session, and with it the gold run",
    "expect": "races the device's reply; tests/test_expect.py drives it",
    "expect.regex": "races the device's reply; tests/test_expect.py drives it",
    "firmware.show": _OPENS_APP,
    "log.diag.dump": _DIAG_RUN,
    "log.diag.list": _DIAG_RUN,
    "log.show": _OPENS_APP,
    "mcp.catalog": "the whole command catalog as JSON; it changes with every command",
    "mcp.info": "prints the process id",
    "os": "runs a shell command, gated by TERMAPY_OS_CMD_ENABLED, a machine setting",
    "ping": _TIMED,
    "port.chip": _HARDWARE,
    "port.chip.description": _HARDWARE,
    "port.chip.list": _HARDWARE,
    "port.list": _HARDWARE,
    "port.usb": _HARDWARE,
    "profile.info": _PATH,
    "proto.crc.c": _CRCGLOT,
    "proto.crc.detect": _CRCGLOT,
    "proto.crc.find": _CRCGLOT,
    "proto.crc.list": _CRCGLOT,
    "proto.crc.reverse": _CRCGLOT,
    "proto.help": "prints the same page as /help proto, which the help section already pins",
    "proto.run": _TIMED,
    "run": "in a script the runner takes /run as a nested script, so bare /run is a usage error there",
    "run.profile": _TIMED,
    "run.profile.cmd": _TIMED,
    "show": _OPENS_APP,
    "show.cfg": _OPENS_APP,
    "stop": "stops the running script, which is the gold run",
    "term": "bare /term sends 'help term' to the device (review 2026-10-15, open)",
    "term.send": "sends without waiting; the reply prints whenever it arrives",
    "term.usb_db": _PATH,
    "xfer.root": _PATH,
    "xfer.xmodem.recv": _TIMED,
    "xfer.xmodem.send": _TIMED,
    "xfer.ymodem.recv": _TIMED,
    "xfer.ymodem.send": _TIMED,
}


def _gold_names(script: str, known: set[str]) -> set[str]:
    """The registered commands a gold script runs: each line's command, and a capture's (``<- /cmd``).

    A level suffix (``/mem.dump.silent``) counts as its command.
    """
    used: set[str] = set()
    for line in script.splitlines():
        for match in re.finditer(r"(?:^\s*|<-\s*)/([\w.]+)", line):
            name = match.group(1)
            while name not in known and "." in name:
                name = name.rsplit(".", 1)[0]
            used.add(name)
    return used


def _gold_gaps() -> tuple[list[str], set[str]]:
    """Families neither in the gold run nor excused; and the excused names actually used."""
    groups = _command_groups()
    used = _gold_names(
        GOLD_RUN.read_text(encoding="utf-8"), {name for names in groups for name in names}
    )
    missing: list[str] = []
    excused: set[str] = set()
    for names in groups:
        if used & set(names):
            continue
        listed = set(names) & NOT_IN_GOLD.keys()
        if listed:
            excused |= listed
        else:
            missing.append(names[0])
    return sorted(missing), excused


@pytest.mark.rule("command-in-gold")
def test_every_command_is_in_the_gold_run_or_says_why():
    """Every command family runs in tests/cli_gold/cli_test.run, or NOT_IN_GOLD says why it cannot."""
    # Arrange / Act
    missing, _ = _gold_gaps()

    # Assert
    assert missing == [], (
        "add a deterministic line to tests/cli_gold/cli_test.run (and its expected output), "
        "or list the family in NOT_IN_GOLD with the reason it cannot run there:\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.rule("command-in-gold")
def test_not_in_gold_has_no_stale_entries():
    """Every NOT_IN_GOLD entry is a real command whose family is still not in the gold run."""
    # Arrange / Act
    _, excused = _gold_gaps()

    # Assert
    stale = sorted(NOT_IN_GOLD.keys() - excused)
    assert stale == [], (
        f"in the gold run now, or no longer a command; drop them from NOT_IN_GOLD: {stale}"
    )


@pytest.mark.rule("command-in-gold")
def test_gold_parser_fires_on_a_probe():
    """The gold parser takes a line's command and a capture's, maps a level suffix to its command, skips prose."""
    # Arrange
    script = "# /not.a.command in a comment\n/mem.dump.silent 0x0 4\n$(X) <- /var NAME\nAT+NAME?\n  /term.hex\n"

    # Act
    used = _gold_names(script, {"mem.dump", "var", "term.hex"})

    # Assert
    assert used == {"mem.dump", "var", "term.hex"}, (
        "commands only; a comment and a device line are not"
    )


# ── Loopback labels ──────────────────────────────────────────────────────────
#
# Two loopbacks, two markers.  sw_loopback is pyserial's virtual loop:// port:
# no hardware, runs anywhere, CI included.  hw_loopback is the TX-RX-jumpered
# adapter: gated in conftest (opt-in flag, refused on GitHub Actions, required
# by release_prep) and pinned by tests/test_hw_loopback_gate.py.  This guard
# keeps the sw label complete, so `-m sw_loopback` selects every test that
# touches loop:// and the two are never read as one thing.

_SW_LOOPBACK_URL = "loop://"


def _mark_names(nodes: list[ast.expr]) -> set[str]:
    """The ``pytest.mark.<name>`` names used anywhere in ``nodes``."""
    return {
        sub.attr
        for node in nodes
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.value, ast.Attribute)
        and sub.value.attr == "mark"
    }


def _module_marks(tree: ast.Module) -> set[str]:
    """The marker names a module's ``pytestmark`` gives every test in it."""
    return _mark_names(
        [
            stmt.value
            for stmt in tree.body
            if isinstance(stmt, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in stmt.targets
            )
        ]
    )


def _loop_url_constants(node: ast.AST, docstrings: set[int]) -> set[int]:
    """Ids of the string constants under ``node`` that name loop://, docstrings excluded."""
    return {
        id(sub)
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant)
        and isinstance(sub.value, str)
        and _SW_LOOPBACK_URL in sub.value
        and id(sub) not in docstrings
    }


def _sw_loopback_unlabeled(tree: ast.Module) -> list[str]:
    """Tests that use loop:// without the sw_loopback marker, and ``<module>`` for unlabeled helper use.

    A test's marks are its own decorators, its classes' decorators and the
    module's ``pytestmark``.  A loop:// constant outside any test (a helper,
    a fixture, module scope) can feed any test, so it needs the module mark.
    """
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    module_marks = _module_marks(tree)

    unlabeled: list[str] = []
    in_tests: set[int] = set()

    def visit(body: list[ast.stmt], prefix: str, inherited: set[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                uses = _loop_url_constants(node, docstrings)
                in_tests.update(uses)
                if uses and "sw_loopback" not in inherited | _mark_names(node.decorator_list):
                    unlabeled.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit(
                    node.body, f"{prefix}{node.name}.", inherited | _mark_names(node.decorator_list)
                )

    visit(tree.body, "", module_marks)
    if _loop_url_constants(tree, docstrings) - in_tests and "sw_loopback" not in module_marks:
        unlabeled.append("<module>")
    return unlabeled


@pytest.mark.rule("sw-loopback-labeled")
def test_sw_loopback_tests_are_labeled():
    """Every test that uses pyserial's loop:// carries the sw_loopback marker.

    This file is skipped: it names the URL to search for.
    """
    # Arrange / Act
    violations = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if path == Path(__file__):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations.extend(f"{path.name}::{name}" for name in _sw_loopback_unlabeled(tree))

    # Assert
    assert violations == [], (
        "these use loop:// without @pytest.mark.sw_loopback (or a module pytestmark):\n  "
        + "\n  ".join(violations)
    )


def test_sw_loopback_guard_fires_on_a_probe():
    """The guard flags an unlabeled test and unlabeled helper use, and passes labels, class marks and docstring mentions."""
    # Arrange -- one of each case in a single module
    source = '''
"""A module docstring may say loop:// freely."""
import pytest

def _helper():
    return "loop://"

@pytest.mark.sw_loopback
def test_labeled():
    open_port("loop://")

def test_unlabeled():
    open_port("loop://")

def test_docstring_only():
    """Mentions loop:// but never opens it."""

@pytest.mark.sw_loopback
class TestMarkedClass:
    def test_inherits(self):
        open_port("loop://")
'''

    # Act
    actual = _sw_loopback_unlabeled(ast.parse(source))

    # Assert
    assert actual == ["test_unlabeled", "<module>"], (
        "the unlabeled test, and the helper with no module mark"
    )


# ── Timing tests run alone ───────────────────────────────────────────────────
#
# A test whose result depends on wall-clock time fails when the parallel
# suite loads the machine, so it carries ``timing`` and runs in a serial
# pass after the parallel one (release_prep, tox, CI).  The mechanical sign
# is an assert that depends on a clock reading; a hardware-loopback test
# already runs alone in its own step.  Found 2026-11-11, when the v0.80.0 release gate
# failed on a different load-raced test each run.

_CLOCK_READS = frozenset({"monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"})
_RUNS_ALONE = frozenset({"timing", "hw_loopback"})


def _reads_the_clock(node: ast.AST, clock_names: set[str]) -> bool:
    """Whether ``node`` calls ``time.monotonic`` / ``perf_counter`` or uses a name holding a clock reading."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in clock_names:
            return True
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id in _CLOCK_READS:
            return True
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _CLOCK_READS
            and isinstance(func.value, ast.Name)
            and func.value.id == "time"
        ):
            return True
    return False


def _bound_names(target: ast.expr) -> list[str]:
    """The names an assignment target binds: a name, or names unpacked from a tuple or list.

    ``reader._last_rx = ...`` or ``table[key] = ...`` binds none: the value
    goes into an object, and ``reader`` itself holds no clock reading.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _bound_names(element)]
    return []


def _asserts_on_the_clock(test: ast.AST) -> bool:
    """Whether an assert in ``test`` depends on a clock reading.

    A name assigned from a clock call holds a reading (``started =
    time.monotonic()``), and so does one assigned from such a name
    (``elapsed = ended - started``), followed to a fixed point across the
    test and its nested functions.  An attribute set from the clock
    (backdating ``reader._last_rx``) or a deadline no assert reads is not a
    timing assert: load cannot change its outcome.
    """
    assigns = [node for node in ast.walk(test) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    clock_names: set[str] = set()
    grew = True
    while grew:
        grew = False
        for assign in assigns:
            if assign.value is None or not _reads_the_clock(assign.value, clock_names):
                continue
            targets = assign.targets if isinstance(assign, ast.Assign) else [assign.target]
            for target in targets:
                for name in _bound_names(target):
                    if name not in clock_names:
                        clock_names.add(name)
                        grew = True
    return any(
        isinstance(node, ast.Assert) and _reads_the_clock(node.test, clock_names)
        for node in ast.walk(test)
    )


def _timing_unmarked(tree: ast.Module) -> list[str]:
    """Tests with an assert that depends on a clock reading, without a marker that runs them alone.

    A test's marks are its own decorators, its classes' decorators and the
    module's ``pytestmark``.
    """
    unmarked: list[str] = []

    def visit(body: list[ast.stmt], prefix: str, inherited: set[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                marks = inherited | _mark_names(node.decorator_list)
                if _asserts_on_the_clock(node) and not marks & _RUNS_ALONE:
                    unmarked.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit(
                    node.body, f"{prefix}{node.name}.", inherited | _mark_names(node.decorator_list)
                )

    visit(tree.body, "", _module_marks(tree))
    return unmarked


@pytest.mark.rule("timing-tests-run-alone")
def test_a_test_that_reads_the_clock_runs_alone():
    """Every test with an assert on a time.monotonic or perf_counter reading carries timing (or hw_loopback).

    This file is skipped: its probe names the clock calls it looks for.
    """
    # Arrange / Act
    violations = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if path == Path(__file__):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations.extend(f"{path.name}::{name}" for name in _timing_unmarked(tree))

    # Assert
    assert violations == [], (
        "these assert on a clock reading, so a loaded machine can fail them; add "
        "@pytest.mark.timing (or a class or module mark) so they run alone:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("timing-tests-run-alone")
def test_the_timing_guard_fires_on_a_probe():
    """The guard flags unmarked asserts on a clock reading, however it is carried, and passes marks and clock uses no assert reads."""
    # Arrange -- one of each case in a single module
    source = """
import time
from time import perf_counter
import pytest

def test_unmarked():
    started = time.monotonic()
    assert time.monotonic() - started < 0.1

def test_bare_import_unmarked():
    assert perf_counter() > 0

def test_carried_through_names_unmarked():
    started = time.monotonic()
    marks = [started, time.monotonic()]
    longest = max(marks) - min(marks)
    assert longest < 3.0

def test_backdates_an_attribute():
    reader._last_rx = time.monotonic() - 0.3
    assert reader.flush() == ["partial"]

def test_a_deadline_no_assert_reads():
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        pass
    assert done

@pytest.mark.timing
def test_marked():
    assert time.monotonic() > 0

@pytest.mark.hw_loopback
def test_hardware():
    assert time.monotonic() > 0

def test_reads_without_assert():
    time.monotonic()

def test_wall_clock_stamp_only():
    assert time.time() > 0

@pytest.mark.timing
class TestMarkedClass:
    def test_inherits(self):
        assert time.monotonic() > 0
"""

    # Act
    actual = _timing_unmarked(ast.parse(source))
    module_marked = _timing_unmarked(ast.parse("pytestmark = pytest.mark.timing\n" + source))

    # Assert
    assert actual == [
        "test_unmarked",
        "test_bare_import_unmarked",
        "test_carried_through_names_unmarked",
    ], "the three unmarked asserts on a reading, nothing else"
    assert module_marked == [], "a module pytestmark covers every test in it"


# ── Hex: one reader for input, one case for output ───────────────────────────
#
# User-typed hex is read by the published readers only: protocol.parse_hex /
# parse_data / parse_pattern for bytes, symbols.parse_number for numbers.
# Both accept any case.  Every hex a command prints or returns is uppercase.
# Until 2026-10-13 four /proto.crc.* commands each had a private tokenizer
# with its own rules, one of which read 0x-spelled hex as text.  The allowlists
# name the exact function, so a module is never excused wholesale.

SRC_REL = SRC  # src/termapy

# (module, function): why it may read hex itself.
HEX_PARSE_ALLOWED: dict[tuple[str, str], str] = {
    ("protocol/core.py", "parse_hex"): "the published hex-only reader",
    (
        "protocol/core.py",
        "_match_hex_byte",
    ): "the one hex-byte tokenizer behind parse_data, parse_data_segments and parse_pattern",
    ("symbols/address.py", "parse_number"): "the published number reader",
    ("port_format.py", "split_vid_pid"): "the USB id reader; USB ids are hex by convention",
    ("demo.py", "FakeSerial._parse_hex_addr"): "the simulated device's own grammar",
    ("demo.py", "FakeSerial._handle_mem_native"): "the simulated device's own grammar",
    ("demo.py", "FakeSerial._handle_mem"): "the simulated device's own grammar",
    ("memory.py", "parse_reply"): "a MEM reply from the device, not user text",
    ("memory.py", "TemplateDialect.parse_read"): "a device's read reply, not user text",
    ("response_parsers.py", "_coerce"): "device response text",
    (
        "builtins/commands/proto.py",
        "_detect_ascii",
    ): "a packet's ASCII-hex CRC trailer, device text",
    ("symbols/converters/xc32.py", "convert"): "a linker map's own address format",
    ("symbols/converters/xc32.py", "regions"): "a linker map's memory table, the same format",
    ("symbols/table.py", "parse_addr_field"): "a symbol file's 0x address field",
    (
        "boards/micropython/firmware/termapy_json.py",
        "int_arg",
    ): "board code run by MicroPython, which cannot import termapy's readers; "
    "test_boards pins that it takes what the profile's int_range takes",
    (
        "boards/micropython/firmware/termapy_mem.py",
        "_hex",
    ): "board code run by MicroPython: the MEM wire spec's own bare-hex addresses and "
    "masks, termapy_mem.c's ParseHexU32; tests/mem_wire_cases.py holds both to one table",
}

# (module, function): why it may render lowercase hex.
HEX_LOWERCASE_ALLOWED: dict[tuple[str, str], str] = {
    ("defaults.py", "_preview_color"): "a #rrggbb theme color, not hex data",
}

_LOWERCASE_PRINTF = re.compile(r"%[-#0 +]*\d*x")


def _scoped_nodes(tree: ast.Module):
    """``(qualname, node)`` for every node, qualname being its enclosing function or class path."""
    found: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{scope}.{child.name}" if scope else child.name
            found.append((name or "<module>", child))
            visit(child, name)

    visit(tree, "")
    return found


def _hex_parse_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every call that reads hex: ``fromhex``, ``unhexlify``, ``int(x, 16)``."""
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("fromhex", "unhexlify", "a2b_hex"):
            sites.append((qualname, node.lineno))
        elif isinstance(func, ast.Name) and func.id == "int":
            base = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "base"), None
                )
            )
            if isinstance(base, ast.Constant) and base.value == 16:
                sites.append((qualname, node.lineno))
    return sites


def _lowercase_hex_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every lowercase hex rendering.

    A ``.hex()`` call not followed by ``.upper()``, an f-string spec ending
    in ``x``, a ``format(..., "...x")``, a ``%x`` string, and builtin ``hex()``.
    """
    parents = {
        id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
    }
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        line = getattr(node, "lineno", 0)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "hex"
        ):
            parent = parents.get(id(node))
            if not (isinstance(parent, ast.Attribute) and parent.attr == "upper"):
                sites.append((qualname, line))
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hex"
        ):
            sites.append((qualname, line))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "format"
        ):
            spec = node.args[1] if len(node.args) > 1 else None
            if (
                isinstance(spec, ast.Constant)
                and isinstance(spec.value, str)
                and spec.value.endswith("x")
            ):
                sites.append((qualname, line))
        elif isinstance(node, ast.FormattedValue) and isinstance(node.format_spec, ast.JoinedStr):
            spec = "".join(
                part.value
                for part in node.format_spec.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if spec.endswith("x"):
                sites.append((qualname, getattr(node.value, "lineno", line)))
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and _LOWERCASE_PRINTF.search(node.left.value)
        ):
            sites.append((qualname, line))
    return sites


def _source_modules():
    """``(relative path, tree)`` for every non-vendored module under src/termapy."""
    for path in sorted(SRC_REL.rglob("*.py")):
        rel = path.relative_to(SRC_REL).as_posix()
        if not rel.startswith("vendor/"):
            yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _unallowed(
    finder, allowed: dict[tuple[str, str], str]
) -> tuple[list[str], set[tuple[str, str]]]:
    """Sites ``finder`` reports outside ``allowed``, and the allowlist entries that matched something."""
    violations: list[str] = []
    used: set[tuple[str, str]] = set()
    for rel, tree in _source_modules():
        for qualname, line in finder(tree):
            if (rel, qualname) in allowed:
                used.add((rel, qualname))
            else:
                violations.append(f"{rel}:{line} in {qualname}")
    return violations, used


@pytest.mark.rule("hex-one-reader")
def test_hex_is_parsed_only_by_the_published_readers():
    """User hex is read only by parse_hex / parse_data / parse_pattern / parse_number.

    Anything else that reads hex is named in HEX_PARSE_ALLOWED with the
    reason it is not user input (device text, a file format, a USB id).
    """
    # Arrange / Act
    violations, _ = _unallowed(_hex_parse_sites, HEX_PARSE_ALLOWED)

    # Assert
    assert violations == [], (
        "read hex with protocol.parse_data / parse_hex (bytes) or symbols.parse_number "
        "(numbers), or allowlist the site with its reason:\n  " + "\n  ".join(violations)
    )


@pytest.mark.rule("hex-output-uppercase")
def test_hex_output_is_uppercase():
    """Every hex termapy prints or returns is uppercase; .hex() is always .hex().upper()."""
    # Arrange / Act
    violations, _ = _unallowed(_lowercase_hex_sites, HEX_LOWERCASE_ALLOWED)

    # Assert
    assert violations == [], (
        "render hex uppercase (.hex().upper(), :02X, format_hex) or allowlist it:\n  "
        + "\n  ".join(violations)
    )


def test_hex_guard_fires_on_a_probe():
    """Both hex guards flag what they exist to catch and pass what they allow."""
    # Arrange -- one of each reader shape and each lowercase shape, plus their correct forms
    source = """
def reads():
    bytes.fromhex(a)
    int(b, 16)
    int(c, base=16)
    binascii.unhexlify(d)
    int(e)

def renders():
    data.hex()
    data.hex(" ")
    hex(n)
    f"{n:02x}"
    format(n, "04x")
    "%02x" % n
    data.hex().upper()
    f"{n:02X}"
"""
    tree = ast.parse(source)

    # Act
    parsed = _hex_parse_sites(tree)
    lowercase = _lowercase_hex_sites(tree)

    # Assert
    assert [line for _, line in parsed] == [3, 4, 5, 6], (
        "fromhex, int(x, 16) both ways, unhexlify; not int(x)"
    )
    assert [line for _, line in lowercase] == [10, 11, 12, 13, 14, 15], (
        "bare .hex() twice, hex(), a lowercase f-spec, format(), printf; not .upper() or :02X"
    )


def test_hex_allowlist_has_no_stale_entries():
    """Every allowlisted function still reads or renders hex, so both allowlists only shrink."""
    # Arrange / Act
    _, parse_used = _unallowed(_hex_parse_sites, HEX_PARSE_ALLOWED)
    _, lower_used = _unallowed(_lowercase_hex_sites, HEX_LOWERCASE_ALLOWED)

    # Assert
    stale = sorted(set(HEX_PARSE_ALLOWED) - parse_used) + sorted(
        set(HEX_LOWERCASE_ALLOWED) - lower_used
    )
    assert stale == [], f"allowlisted but no longer a hex site; drop them: {stale}"


# ── Handler naming: cookie-cutter plugins ────────────────────────────────────
#
# A command's handler is `_handler` for the root and `_handler_<sub>` for a
# subcommand (`_handler_crc_calc` for /proto.crc.calc), in the built-ins, the
# demo plugins and the examples alike, so adding a command is copying one
# shape.  Until 2026-10-13 five styles were in use (`_cmd_send`, `_crc_list`,
# `_proto_root_handler`, `_icon_handler`, ...), three of them in proto.py.
# A handler built by a factory (a call, a lambda) is not a name and not
# checked: the folder-op and per-language families are generated.

PLUGIN_FOLDERS = (
    SRC / "builtins" / "commands",
    SRC / "builtins" / "demo" / "plugin",
    SRC.parent.parent / "examples" / "plugins",
)
_HANDLER_NAME = re.compile(r"^_handler(_[a-z0-9]+)*$")


def _misnamed_handlers(tree: ast.Module) -> list[str]:
    """Names passed as ``Command(handler=...)`` that break the ``_handler`` / ``_handler_<sub>`` shape."""
    return sorted(
        {
            keyword.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Command"
            for keyword in node.keywords
            if keyword.arg == "handler"
            and isinstance(keyword.value, ast.Name)
            and not _HANDLER_NAME.match(keyword.value.id)
        }
    )


@pytest.mark.rule("handler-naming")
def test_handlers_follow_one_naming_shape():
    """Every command handler is `_handler` or `_handler_<sub>`, in built-ins, demo and example plugins."""
    # Arrange / Act
    violations = [
        f"{path.relative_to(SRC.parent.parent).as_posix()}: {name}"
        for folder in PLUGIN_FOLDERS
        for path in sorted(folder.glob("*.py"))
        for name in _misnamed_handlers(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "name a command handler _handler (root) or _handler_<sub> (subcommand):\n  "
        + "\n  ".join(violations)
    )


def test_handler_naming_guard_fires_on_a_probe():
    """The guard flags the old styles and passes the shape, a nested subcommand, and a factory-built handler."""
    # Arrange
    source = """
COMMAND = Command(name="x", handler=_handler, sub_commands={
    "a": Command(handler=_handler_a),
    "b": Command(handler=_handler_crc_calc),
    "c": Command(handler=_cmd_send),
    "d": Command(handler=_proto_root_handler),
    "e": Command(handler=_make_list_handler("x", "*")),
    "f": Command(handler=_HandlerTypo),
})
"""

    # Act
    actual = _misnamed_handlers(ast.parse(source))

    # Assert
    assert actual == ["_HandlerTypo", "_cmd_send", "_proto_root_handler"], (
        "old styles and a case typo are flagged; the shape and factories pass"
    )


# ── Markup: text is escaped on its way in ────────────────────────────────────
#
# Rich reads `[word]` as a style tag whenever word starts lowercase, `#`, `/`
# or `@`, and drops one it does not know without complaint: until 2026-10-13
# `/var` listed `$(TAG) = [ok] boot` as `$(TAG) =  boot` and `/help proto` said
# "[] sections".  A string is markup when its literal text carries a close tag
# (`[/]`, `[/u]`) or it is handed straight to a markup sink.  Every value
# interpolated into one is markup already -- `escape_markup(x)`, or a name or a
# call ending in `_markup` -- a style in a tag slot (`[{color}]`), or a number
# (a numeric format spec, `{n:3d}`, `{v:02X}`).  A markup template filled by
# `.format()` or `%` hides its interpolations from this scan, so it is refused.
# The reverse is refused too: markup handed to a plain channel (`ctx.io.output`)
# shows its tags as text, as the demo traffic plugin's counts did.

MARKUP_SINKS = frozenset(
    {
        "result_markup",
        "output_markup",
        "status_markup",
        "_write_markup",
        "write_markup",
        "_write_output_markup",
        "from_markup",
    }
)
PLAIN_CHANNELS = frozenset({"result", "output", "status"})
_NUMERIC_SPEC = frozenset("bcdeEfFgGnoxX%")
MARKUP_SCAN_FOLDERS = (SRC, SRC.parent.parent / "examples" / "plugins")


def _literal(node: ast.AST | None) -> str:
    """The text of a string constant, or "" for anything else."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _named_markup(node: ast.expr) -> bool:
    """True for ``escape_markup(x)``, ``x_markup`` and ``f_markup(...)``: markup by name."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id.endswith("_markup")
    return isinstance(target, ast.Attribute) and target.attr.endswith("_markup")


def _numeric(part: ast.FormattedValue) -> bool:
    """True when the format spec only a number accepts (``3d``, ``02X``, ``.1f``)."""
    spec = part.format_spec
    tail = _literal(spec.values[-1]) if isinstance(spec, ast.JoinedStr) and spec.values else ""
    return bool(tail) and tail[-1] in _NUMERIC_SPEC


def _raw_interpolations(fstring: ast.JoinedStr) -> list[ast.FormattedValue]:
    """The interpolations of a markup f-string that are not markup, a tag slot, or a number."""
    parts = fstring.values
    raw: list[ast.FormattedValue] = []
    for i, part in enumerate(parts):
        if not isinstance(part, ast.FormattedValue):
            continue
        before = _literal(parts[i - 1]) if i else ""
        after = _literal(parts[i + 1]) if i + 1 < len(parts) else ""
        if before.endswith("[") and after.startswith("]"):
            continue  # a style: [{color}]
        if part.conversion == -1 and (_named_markup(part.value) or _numeric(part)):
            continue
        raw.append(part)
    return raw


def _unescaped_markup_sites(tree: ast.Module) -> list[tuple[int, str]]:
    """``(line, what)`` for every value that reaches markup unescaped, and every opaque template."""
    templates = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and "[/" in _literal(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    sink_args = {
        id(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and node.args
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) in MARKUP_SINKS
    }
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # A placeholder stands where each value goes, so "[{step}/{total}]" is not "[/]".
            shape = "".join(_literal(part) or "\0" for part in node.values)
            if id(node) in sink_args or "[/" in shape:
                sites.extend(
                    (part.lineno, ast.unparse(part.value)) for part in _raw_interpolations(node)
                )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            receiver = node.func.value
            if "[/" in _literal(receiver) or (
                isinstance(receiver, ast.Name) and receiver.id in templates
            ):
                sites.append((node.lineno, f"{ast.unparse(receiver)}.format(...)"))
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and "[/" in _literal(node.left)
        ):
            sites.append((node.lineno, "markup % ..."))
        if (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in PLAIN_CHANNELS
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "io"
        ):
            arg = node.args[0]
            parts = arg.values if isinstance(arg, ast.JoinedStr) else [arg]
            if "[/" in "".join(_literal(part) or "\0" for part in parts):
                sites.append((node.lineno, f"markup on the plain {node.func.attr} channel"))
    return sorted(sites)


def _markup_scan_modules():
    """``(relative path, tree)`` for every module the markup rule covers."""
    root = SRC.parent.parent
    for folder in MARKUP_SCAN_FOLDERS:
        for path in sorted(folder.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/vendor/" not in rel:
                yield rel, ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.rule("markup-escape")
def test_text_reaches_markup_escaped():
    """Every value interpolated into Rich markup is escape_markup()'d, `_markup`-named, a style, or a number."""
    # Arrange / Act
    violations = [
        f"{rel}:{line}: {what}"
        for rel, tree in _markup_scan_modules()
        for line, what in _unescaped_markup_sites(tree)
    ]

    # Assert
    assert violations == [], (
        "wrap text in termapy.markup.escape_markup(), or name a value that is markup "
        "already *_markup; build markup with an f-string, never .format() or %:\n  "
        + "\n  ".join(violations)
    )


def test_markup_guard_fires_on_a_probe():
    """The markup guard flags raw values, conversions, templates and markup on a plain channel; it passes the rest."""
    # Arrange -- each flagged shape, then each allowed one
    source = """
_TEMPLATE = "[bold]{text}[/]"

def render(ctx, name, n, color, value_markup):
    ctx.io.output_markup(f"  [cyan]{name}[/]")
    ctx.io.output_markup(f"  {name}")
    line = f"[green]{escape_markup(name)!r}[/]"
    _TEMPLATE.format(text=name)
    "[red]%s[/]" % name
    ctx.io.output(f"  TX: [yellow]{n:d}[/]")
    ctx.io.output_markup(f"[{color}]{escape_markup(name)}[/]")
    ctx.io.output_markup(f"[dim]{n:3d} {value_markup} {make_markup(n)}[/]")
    plain = f"plain {name} [1/3]"
    progress = f"[{n}/{total}] {name}"
    ctx.io.output(f"[{name}]")
"""

    # Act
    actual = [line for line, _ in _unescaped_markup_sites(ast.parse(source))]

    # Assert
    assert actual == [5, 6, 7, 8, 9, 10], (
        "a raw value in a tagged f-string, any raw value handed to a sink, a converted "
        "escape, a .format() template, a % template and markup on a plain channel are "
        "flagged; nothing else is"
    )


# ── Widgets: text never reaches Textual's markup parser ──────────────────────
#
# Textual's widgets parse Textual's own markup, not Rich's, and it has no
# clean escape: `[Rec]` and `[$var]` vanish though Rich's escape leaves them
# alone, and a trailing backslash still eats the close tag.  So a widget built
# from anything but a string the author wrote takes `markup=False`, or a
# `rich.text.Text`, which no parser reads.  The constructors and `notify` are
# checked here; a later `.update()` / `.tooltip =` / `.label =` is not
# syntactically distinguishable from data and stays review (registry
# widget-text-literal).

_MARKUP_KW_WIDGETS = frozenset({"Static", "Label"})


def _call_name(node: ast.Call) -> str:
    """The called name: ``Static`` for ``Static(...)``, ``notify`` for ``self.notify(...)``."""
    func = node.func
    return (
        func.id
        if isinstance(func, ast.Name)
        else func.attr
        if isinstance(func, ast.Attribute)
        else ""
    )


def _authored(node: ast.expr, constants: set[str]) -> bool:
    """True for a string the author wrote (a literal, or a module-level string constant) or a ``Text``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Name) and node.id in constants:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        return (isinstance(func, ast.Name) and func.id == "Text") or (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "Text"
        )
    return False


def _widget_markup_sites(tree: ast.Module) -> list[tuple[int, str]]:
    """``(line, call)`` for every widget or notification built from text that Textual would parse."""
    constants = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and _literal(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _MARKUP_KW_WIDGETS | {"Button", "notify"}:
            continue
        keyword = {"Button": "label", "notify": "message"}.get(name, "content")
        content = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == keyword), None)
        )
        markup_off = any(
            kw.arg == "markup" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in node.keywords
        )
        if content is None or markup_off or _authored(content, constants):
            continue
        sites.append((node.lineno, f"{name}({ast.unparse(content)})"))
    return sorted(sites)


@pytest.mark.rule("widget-text-literal")
def test_widget_text_is_never_parsed_as_markup():
    """A Static, Label, Button or notify built from text has markup=False or a Text."""
    # Arrange / Act
    violations = [
        f"{rel.as_posix()}:{line}: {call}"
        for path in sorted(SRC.rglob("*.py"))
        if _is_ui_layer(rel := path.relative_to(SRC))
        for line, call in _widget_markup_sites(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "pass markup=False (Static, Label, notify) or wrap the text in rich.text.Text "
        "(Button has no markup switch):\n  " + "\n  ".join(violations)
    )


def test_widget_guard_fires_on_a_probe():
    """The widget guard flags text built into each widget and notify; it passes literals, constants, Text and markup=False."""
    # Arrange -- each flagged shape, then each allowed one
    source = """
_TITLE = "[b]Pick a config[/]"

def compose(self, name, path):
    yield Static(name)
    yield Label(f"Port: {path}")
    yield Button(name)
    self.notify(f"Saved {path}")
    yield Static(content=name)
    yield Static("[b]Literal[/]")
    yield Static(_TITLE)
    yield Static(name, markup=False)
    yield Button(Text(name))
    self.notify(f"Saved {path}", markup=False)
    yield Label(Text.assemble(("x", "bold"), name))
"""

    # Act
    actual = [line for line, _ in _widget_markup_sites(ast.parse(source))]

    # Assert
    assert actual == [5, 6, 7, 8, 9], (
        "text into Static, Label, Button, notify and a content= keyword is flagged; "
        "literals, module constants, markup=False and Text pass"
    )


# ── Status pages render through one view ─────────────────────────────────────
#
# A status page returns ``CmdResult.record(data, rows, ...)``: the facts are
# ``data`` and the label/value rows a person reads are a ``RecordView``,
# printed by dispatch unless the caller wants data.  ``format_kv_lines`` is
# that view's renderer, so a call anywhere else is a handler printing its own
# rows, which then ride into every JSON answer beside ``data`` (the
# /mcp.info envelope that carried its facts as display strings).
KV_RENDERER = "termapy/plugins/views.py"
# Files that still render rows themselves; the list only shrinks.
KNOWN_KV_CALLERS: frozenset[str] = frozenset()


def _kv_calls(tree: ast.AST, rel: str) -> list[str]:
    """Every call to ``format_kv_lines`` in ``rel``, bare or as an attribute."""
    return [
        f"{rel}:{node.lineno}  {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "format_kv_lines")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "format_kv_lines")
        )
    ]


def _kv_caller_files() -> dict[str, list[str]]:
    """``{rel: [call lines]}`` for every package file that calls ``format_kv_lines``."""
    found: dict[str, list[str]] = {}
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        calls = _kv_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
        if calls:
            found[rel] = calls
    return found


@pytest.mark.rule("record-one-format")
def test_status_rows_render_only_in_the_record_view():
    """Only the record view calls ``format_kv_lines``; a status page returns ``CmdResult.record``."""
    # Arrange / Act
    violations = [
        line
        for rel, calls in _kv_caller_files().items()
        if rel != KV_RENDERER and rel not in KNOWN_KV_CALLERS
        for line in calls
    ]

    # Assert
    assert violations == [], (
        "a status page returns CmdResult.record(data, rows, value=...) and lets "
        "dispatch render the rows; it never calls format_kv_lines itself:\n  "
        + "\n  ".join(violations)
    )


def test_kv_guard_fires_on_a_probe():
    """The guard flags a bare and a qualified format_kv_lines call, and not a record answer."""
    # Arrange
    probe = (
        "lines = format_kv_lines(rows)\n"
        "lines = output_levels.format_kv_lines(rows)\n"
        "return CmdResult.record(data, rows, value='1')\n"
    )

    # Act
    found = _kv_calls(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 2, "both renderer calls flagged; the record answer is not"


def test_kv_callers_has_no_stale_entries():
    """Every file in KNOWN_KV_CALLERS still calls format_kv_lines, and the view still does."""
    # Arrange / Act
    actual = set(_kv_caller_files())

    # Assert
    stale = sorted(KNOWN_KV_CALLERS - actual)
    assert stale == [], (
        "KNOWN_KV_CALLERS lists files that no longer render rows; drop them:\n  "
        + "\n  ".join(stale)
    )
    assert KV_RENDERER in actual, "the record view is where format_kv_lines is called"


# ── A failure is reported once ───────────────────────────────────────────────
#
# Review 2026-10-15 (S4): an error had three owners -- the handler's own red
# line, a host status line and dispatch's exit -- so a failure showed twice,
# and the workaround, ``CmdResult.fail(msg="")``, emptied the JSON ``error``
# an agent branches on.  Dispatch's exit is the one reporter: a handler
# returns ``CmdResult.fail(msg)`` with the reason and prints none of it, and a
# caller answering with a nested dispatch's result passes ``report=False``
# (``ctx.internal.dispatch`` always does), so the failure prints once, at the
# caller's exit.  A hint printed beside a failure is not the failure and passes.

# (module, function): why it shows a failure itself.  These ARE the reporter.
FAILURE_PRINTERS: dict[tuple[str, str], str] = {
    (
        "repl.py",
        "ReplEngine.dispatch_full",
    ): "a line's own exit on the paths that never reach a handler; silent under report=False",
    (
        "app.py",
        "SerialTerminal._dispatch_quiet",
    ): "the find-bar buttons' entry point; its callers discard the result, so the Busy line is the only report",
}

_OUTPUT_SINKS = frozenset(
    {
        "output",
        "output_markup",
        "result",
        "result_markup",
        "status",
        "status_markup",
        "output_segments",
        "output_markup_segments",
        "_write",
        "_write_markup",
        "_write_segments",
        "write",
        "notify",
    }
)
_ERROR_COLORS = frozenset({"red", "yellow"})


def _is_fail_call(node: ast.AST | None) -> bool:
    """True for ``<anything>.fail(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fail"
    )


def _fail_message(call: ast.Call) -> ast.expr | None:
    """The message a ``.fail(...)`` call passes, positional or ``msg=``."""
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg == "msg"), None)


def _sink_name(call: ast.Call) -> str:
    """The called name, bare or the attribute (``ctx.io.output`` -> ``output``)."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _empty_failures(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every failure built with no message.

    ``.fail()``, ``.fail("")``, ``.fail(msg="")``, and a ``CmdResult(...)``
    built with ``success=False`` and no non-empty ``error``.
    """
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_fail_call(node):
            message = _fail_message(node)
            if message is None or (isinstance(message, ast.Constant) and message.value == ""):
                sites.append((qualname, node.lineno))
        elif isinstance(node.func, ast.Name) and node.func.id == "CmdResult":
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            success = keywords.get("success")
            error = keywords.get("error")
            if (
                isinstance(success, ast.Constant)
                and success.value is False
                and (error is None or (isinstance(error, ast.Constant) and error.value == ""))
            ):
                sites.append((qualname, node.lineno))
    return sites


def _printed_failures(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every output call that prints the failure returned next.

    The statement directly before a ``return <x>.fail(...)`` in the same
    block, when it is an output call with a red or yellow color, or one
    whose text is the failure's own message.
    """
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for previous, statement in zip(block, block[1:]):
                if not (isinstance(statement, ast.Return) and _is_fail_call(statement.value)):
                    continue
                if not (isinstance(previous, ast.Expr) and isinstance(previous.value, ast.Call)):
                    continue
                call = previous.value
                name = _sink_name(call)
                if name not in _OUTPUT_SINKS and not name.endswith("_status"):
                    continue
                assert isinstance(statement.value, ast.Call), "checked by _is_fail_call"
                message = _fail_message(statement.value)
                colored = any(
                    isinstance(arg, ast.Constant) and arg.value in _ERROR_COLORS
                    for arg in [*call.args, *(keyword.value for keyword in call.keywords)]
                )
                repeated = (
                    message is not None
                    and bool(call.args)
                    and ast.dump(call.args[0]) == ast.dump(message)
                )
                if colored or repeated:
                    sites.append((qualname, previous.lineno))
    return sites


@pytest.mark.rule("failure-reported-once")
def test_a_failure_carries_its_message():
    """No failure is built with an empty message, so ``error`` always says why."""
    # Arrange / Act
    violations, _ = _unallowed(_empty_failures, {})

    # Assert
    assert violations == [], (
        "a failure carries its reason in error; to avoid printing it twice, return the "
        "nested result (report=False) instead of blanking it:\n  " + "\n  ".join(violations)
    )


@pytest.mark.rule("failure-reported-once")
def test_a_handler_never_prints_the_failure_it_returns():
    """dispatch's exit prints a failure; nothing prints it beside the return."""
    # Arrange / Act
    violations, _ = _unallowed(_printed_failures, FAILURE_PRINTERS)

    # Assert
    assert violations == [], (
        "return CmdResult.fail(msg) and let dispatch print it once; a hint line in the "
        "default color is fine:\n  " + "\n  ".join(violations)
    )


def test_failure_guard_fires_on_a_probe():
    """Both failure guards flag an empty failure and a printed one, and pass a hint and a reason."""
    # Arrange -- each forbidden shape, then the correct forms
    source = """
def handler(ctx, args):
    if a:
        return CmdResult.fail(msg="")
    if b:
        return CmdResult.fail()
    if c:
        return CmdResult(success=False, error="", value="")
    if d:
        ctx.io.output(err, "red")
        return CmdResult.fail(msg=err)
    if e:
        app._status("No log file to delete.", "yellow")
        return CmdResult.fail(msg="No log file.")
    if f:
        ctx.io.output("No profile files found.")
        return CmdResult.fail(msg="No profile files found.")
    if g:
        ctx.io.output("Use /proto.crc.list to see available algorithms.")
        return CmdResult.fail(msg=f"Unknown algorithm: {name}")
    return CmdResult.fail(msg="Not connected.")
"""
    tree = ast.parse(source)

    # Act
    empty = [line for _, line in _empty_failures(tree)]
    printed = [line for _, line in _printed_failures(tree)]

    # Assert
    assert empty == [4, 6, 8], "fail(msg=''), bare fail() and a hand-built failure with no error"
    assert printed == [10, 13, 16], (
        "a red line, a yellow status and a repeated message; not the hint"
    )


# ── A value is a scalar ──────────────────────────────────────────────────────
#
# Audit 2026-11-06: /cap.wire, the demo /traffic.* commands and the MCP gate's
# three refusals answered with a dict as ``value``, the scalar a script
# captures, while ``data`` -- what an agent reads -- was empty.  A value is
# one string or number; several facts are ``data``.  This catches the literal
# shapes, a dict or list written at the call; a variable holding one is review.

_SHAPED_LITERALS = (ast.Dict, ast.List, ast.Set, ast.Tuple, ast.DictComp, ast.ListComp, ast.SetComp)


def _shaped_values(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every ``CmdResult`` built with a dict, list, set or tuple literal as ``value``."""
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        builds = (isinstance(func, ast.Name) and func.id == "CmdResult") or (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "CmdResult"
        )
        if not builds:
            continue
        value = next((keyword.value for keyword in node.keywords if keyword.arg == "value"), None)
        if isinstance(value, _SHAPED_LITERALS):
            sites.append((qualname, node.lineno))
    return sites


@pytest.mark.rule("value-is-scalar")
def test_a_value_is_a_scalar():
    """No CmdResult carries a dict or list as value; the structure is data."""
    # Arrange / Act
    violations, _ = _unallowed(_shaped_values, {})

    # Assert
    assert violations == [], (
        "value is the one scalar a script captures; put the structure in data= and pick "
        "one fact for value=:\n  " + "\n  ".join(violations)
    )


def test_scalar_value_guard_fires_on_a_probe():
    """The value-is-scalar guard flags a dict, a list and a comprehension as value, and passes data."""
    # Arrange -- each forbidden shape, then the correct forms
    source = """
def handler(ctx, args):
    if a:
        return CmdResult.ok(value={"tx": 1})
    if b:
        return CmdResult.fail(msg="No.", value=[1, 2])
    if c:
        return CmdResult(value={k: v for k, v in pairs})
    if d:
        return CmdResult.ok(value="5", data={"tx": 1})
    return CmdResult.ok(value=str(count))
"""
    tree = ast.parse(source)

    # Act
    actual = [line for _, line in _shaped_values(tree)]

    # Assert
    assert actual == [4, 6, 8], "a dict, a list and a comprehension; not data, not a scalar"


def test_failure_printers_has_no_stale_entries():
    """Every FAILURE_PRINTERS function still prints a failure, so the list only shrinks."""
    # Arrange / Act
    _, used = _unallowed(_printed_failures, FAILURE_PRINTERS)

    # Assert
    stale = sorted(set(FAILURE_PRINTERS) - used)
    assert stale == [], f"allowlisted but no longer printing a failure; drop them: {stale}"


# ── A missing config or port is answered by the gate ─────────────────────────
#
# 2026-10-16: some thirty handlers each checked ``ctx.config_path`` and spelled
# "No config loaded." themselves.  A command that needs a loaded config or an
# open port declares it (``CapabilitySet.CONFIG_LOADED`` / ``.SERIAL_CONNECTED``)
# and the dispatch gate answers from ``capabilities.STATE_MESSAGES``, so no
# handler checks the state.  A handler checks it itself only where one branch
# needs it and the rest of the command does not, and answers in the table's
# words; each such site is listed here with its branch.  (The ``/port.*``
# setters refuse a set the same way, their query needing no port, through
# ``port_control._not_connected``, which returns lines rather than a failure.)

# (module, function): the branch that needs the state, or why the gate cannot.
STATE_CHECKED_IN_A_BRANCH: dict[tuple[str, str], str] = {
    ("builtins/commands/proto.py", "_handler_send"): "--dry-run sends nothing",
    ("builtins/commands/proto.py", "_handler_crc_find"): "only cmd= mode talks to the device",
    ("builtins/commands/proto.py", "_handler_crc_reverse"): "only cmd= mode talks to the device",
    ("builtins/commands/proto.py", "_crc_codegen"): "only file= writes, into the config's folder",
    (
        "builtins/commands/var.py",
        "_handler_capture",
    ): "only a device-command capture sends; a slash command does not",
    (
        "builtins/commands/profile.py",
        "_load_from_device",
    ): "only cmd= reads the profile from the device",
    (
        "builtins/commands/profile.py",
        "_handler_load",
    ): "only a bare reload needs a loaded profile; a path or cmd= loads one",
    (
        "repl.py",
        "ReplEngine.dispatch_full",
    ): "a bare device line is no command, so it has no needs= to declare",
}


def _is_state_answer(node: ast.expr | None) -> bool:
    """True for a ``STATE_MESSAGES`` answer, spelled, read from the table, or built on it in an f-string."""
    if isinstance(node, ast.Constant):
        return node.value in STATE_MESSAGES.values()
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(part, ast.FormattedValue) and _is_state_answer(part.value)
            for part in node.values
        )
    if isinstance(node, ast.Call) and _sink_name(node) == "state_message":
        return True  # the prefix-filling reader of the same table
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "STATE_MESSAGES"
    )


def _state_answers(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every failure that answers with a ``STATE_MESSAGES`` state."""
    return [
        (qualname, node.lineno)
        for qualname, node in _scoped_nodes(tree)
        if isinstance(node, ast.Call)
        and _is_fail_call(node)
        and _is_state_answer(_fail_message(node))
    ]


@pytest.mark.rule("state-answered-by-gate")
def test_a_missing_config_or_port_is_answered_by_the_gate():
    """No handler answers "No config loaded." or "Not connected."; its command declares the need."""
    # Arrange / Act -- the examples too: they are what a plugin author copies
    violations, _ = _unallowed(_state_answers, STATE_CHECKED_IN_A_BRANCH)
    examples = SRC.parent.parent / "examples" / "plugins"
    violations += [
        f"examples/plugins/{path.name}:{line} in {qualname}"
        for path in sorted(examples.glob("*.py"))
        for qualname, line in _state_answers(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "declare needs=CapabilitySet.CONFIG_LOADED / .SERIAL_CONNECTED and drop the check, "
        "or list a branch-only check in STATE_CHECKED_IN_A_BRANCH:\n  " + "\n  ".join(violations)
    )


def test_state_answer_guard_fires_on_a_probe():
    """The guard flags each state answer, spelled, read or built on in an f-string, and passes other states."""
    # Arrange
    source = """
def handler(ctx, args):
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    if not ctx.serial.is_connected():
        return CmdResult.fail("Not connected.")
    if a:
        return CmdResult.fail(msg="No log file.")
    if b:
        return CmdResult.fail(msg=refusal)
    if c:
        return CmdResult.fail(msg=f"{STATE_MESSAGES['profile_loaded']}  Nothing to reload.")
    if d:
        return CmdResult.fail(msg=f"Unknown symbol: {name}")
    if e:
        return CmdResult.fail(msg=state_message("capture_idle", ctx.prefix))
    return CmdResult.fail(msg=STATE_MESSAGES["serial_connected"])
"""

    # Act
    lines = [line for _, line in _state_answers(ast.parse(source))]

    # Assert
    assert lines == [4, 6, 12, 16, 17], (
        "spelled, read, read through state_message, or built on; "
        "not another state, a variable or another f-string"
    )


def test_state_checked_in_a_branch_has_no_stale_entries():
    """Every STATE_CHECKED_IN_A_BRANCH function still checks the state, so no entry outlives its branch."""
    # Arrange / Act
    _, used = _unallowed(_state_answers, STATE_CHECKED_IN_A_BRANCH)

    # Assert
    stale = sorted(set(STATE_CHECKED_IN_A_BRANCH) - used)
    assert stale == [], f"listed but no longer checking the state; drop them: {stale}"


# ── The state answers are spelled once ──────────────────────────────────────
#
# 2026-10-16: "Not connected." was spelled by port_control (seven times), the
# serial engine (four, without the period), TerminalHost (twice) and /help's
# port line, besides the gate's table.  Each answer is written once, on the
# capability field that declares it (``_restricted(..., state=)``), and
# everything that says it reads ``capabilities.STATE_MESSAGES``, the view of
# those fields; a copy, even one that drops the period, fails.
STATE_MESSAGES_OWNER = "plugins/capabilities.py"


def _state_answer_copies(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every string that is a ``STATE_MESSAGES`` answer, with or without its period."""
    answers = {answer.rstrip(".") for answer in STATE_MESSAGES.values()}
    return [
        (qualname, node.lineno)
        for qualname, node in _scoped_nodes(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.rstrip(".") in answers
    ]


@pytest.mark.rule("state-messages-one-owner")
def test_the_state_answers_are_spelled_only_on_their_fields():
    """ "No config loaded." and "Not connected." are spelled only on their capability fields."""
    # Arrange / Act
    violations = [
        f"{rel}:{line} in {qualname}"
        for rel, tree in _source_modules()
        if rel != STATE_MESSAGES_OWNER
        for qualname, line in _state_answer_copies(tree)
    ]

    # Assert
    assert violations == [], (
        "read the answer from capabilities.STATE_MESSAGES instead of spelling it:\n  "
        + "\n  ".join(violations)
    )


def test_state_answer_copy_guard_fires_on_a_probe():
    """The guard flags a copy in any call, period or not, and passes the table read and prose that mentions it."""
    # Arrange
    source = '''
def toggle(port):
    """Raises OSError ("Not connected.") with no port."""
    if port is None:
        raise OSError("Not connected")
    status("No config loaded.", "yellow")
    status("Not connected yet, retrying")
    return STATE_MESSAGES["serial_connected"]
'''

    # Act
    lines = [line for _, line in _state_answer_copies(ast.parse(source))]

    # Assert
    assert lines == [5, 6], "both copies; not the docstring, a longer sentence or the table read"


# ── The session log has one owner per frontend ────────────────────────────────
#
# Review 2026-10-15 (D3): six places worked out "the session log" from the
# cfg, and they disagreed -- over MCP /log.dump read the TUI's log, and the
# CLI's $(CFG.LOG_FILE) ignored log_file.  Each frontend answers
# ``session_log_path()`` (``ctx.internal.session_log_path`` for a plugin);
# the TUI's rule is ``config.session_log_file``.  Nothing else calls
# ``cfg_log_path`` or reads the ``log_file`` key.
SESSION_LOG_RULE_MODULE = "config.py"


def _session_log_derivations(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every ``cfg_log_path(...)`` call and every read of the ``log_file`` key."""
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "cfg_log_path":
                sites.append((qualname, node.lineno))
            elif (
                name == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "log_file"
            ):
                sites.append((qualname, node.lineno))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "log_file"
        ):
            sites.append((qualname, node.lineno))
    return sites


@pytest.mark.rule("session-log-one-owner")
def test_the_session_log_is_resolved_in_one_place():
    """Only config.py derives the session log from the cfg; everything else asks the frontend."""
    # Arrange / Act
    violations = [
        f"{rel}:{line} in {qualname}"
        for rel, tree in _source_modules()
        if rel != SESSION_LOG_RULE_MODULE
        for qualname, line in _session_log_derivations(tree)
    ]

    # Assert
    assert violations == [], (
        "ask the frontend which file its session log is (ctx.internal.session_log_path() "
        "in a plugin, host.session_log_path() in a host); never derive it from the cfg:\n  "
        + "\n  ".join(violations)
    )


def test_session_log_guard_fires_on_a_probe():
    """The guard flags cfg_log_path calls and log_file reads, and passes a declaration and a set of key names."""
    # Arrange
    source = """
def derive(ctx, cfg):
    a = cfg_log_path(ctx.config_path)
    b = config.cfg_log_path(path)
    c = ctx.cfg.get("log_file", "")
    d = cfg["log_file"]
    keys = frozenset({"log_file", "profile_path"})
    defaults = {"log_file": ""}
    return ctx.internal.session_log_path()
"""

    # Act
    actual = [line for _, line in _session_log_derivations(ast.parse(source))]

    # Assert
    assert actual == [3, 4, 5, 6], "two calls and two reads; not a key set, a default or the owner"


# ── One handler per command name, in every frontend ──────────────────────────
#
# Review 2026-10-15 (D5): the TUI registered its own /help.open, /cfg.load
# and /edit family over the commands it already had, and the copies drifted
# -- "/edit foo.py" worked in the CLI and failed in the TUI.  A frontend
# changes HOW a command does its job through a host method or a handle
# field (``_switch_to_cfg_path``, ``ctx.internal.edit_file``), never by
# registering a second handler under the same name.  This builds the TUI
# (headless), CLI and MCP command tables and compares each name's handler
# code.  The allowlist names what still differs and why; it only shrinks.
KNOWN_DIVERGENT_COMMANDS: dict[str, str] = {
    "cli": "switches to the CLI: a different act in each frontend",
    "tui": "switches to the TUI: a different act in each frontend",
    "ss.svg": "a stub builtin the TUI replaces (Chuck 2026-10-16: convert later)",
    "ss.txt": "a stub builtin the TUI replaces (convert later)",
}


def _handler_identity(handler) -> object:
    """The code a handler runs: a bound method and a plain function compare by their code object."""
    function = getattr(handler, "__func__", handler)
    return getattr(function, "__code__", None) or id(function)


def _frontend_plugins(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """``{frontend: {command name: PluginInfo}}`` for the TUI, CLI and MCP, each booted for real."""
    import asyncio
    import json

    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    def make_cfg(name: str) -> tuple[dict, str]:
        default_serial = DEFAULT_CFG["serial"]
        assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
        cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": ""}}
        path = tmp_path / name / f"{name}.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(cfg))
        return cfg, str(path)

    tables: dict[str, dict[str, Any]] = {}
    cfg, path = make_cfg("cli")
    cli = CLITerminal(cfg, path, no_color=True)
    cli.repl.global_root = tmp_path
    tables["CLI"] = dict(cli.repl._plugins)

    mcp = pytest.importorskip("termapy.mcp.server", reason="mcp SDK not installed")
    cfg, path = make_cfg("mcp")
    tables["MCP"] = dict(mcp.MCPHost(cfg, path, verbose=False).repl._plugins)

    from termapy.app import SerialTerminal

    cfg, path = make_cfg("tui")
    app = SerialTerminal(cfg, path)
    app.repl.global_root = tmp_path

    async def boot() -> None:
        async with app.run_test() as pilot:
            await pilot.pause()  # the TUI registers its hooks on mount
            tables["TUI"] = dict(app.repl._plugins)

    asyncio.run(boot())
    return tables


def _divergent_commands(tables: dict[str, dict[str, object]]) -> dict[str, list[str]]:
    """``{command name: [frontends]}`` for every name two frontends register with different handlers."""
    names = set().union(*tables.values())
    divergent: dict[str, list[str]] = {}
    for name in sorted(names):
        holders = {frontend: table[name] for frontend, table in tables.items() if name in table}
        if len(set(holders.values())) > 1:
            divergent[name] = sorted(holders)
    return divergent


@pytest.fixture(scope="module")
def frontend_plugins(tmp_path_factory) -> dict[str, dict[str, Any]]:
    return _frontend_plugins(tmp_path_factory.mktemp("frontends"))


@pytest.fixture(scope="module")
def frontend_tables(frontend_plugins) -> dict[str, dict[str, object]]:
    """``{frontend: {command name: handler identity}}``."""
    return {
        frontend: {name: _handler_identity(info.handler) for name, info in plugins.items()}
        for frontend, plugins in frontend_plugins.items()
    }


def _names_on_suffix_words(frontend_plugins: dict[str, dict[str, object]]) -> list[str]:
    """``frontend:name`` of every command whose name ends in ``.json`` or a level word."""
    from termapy.defaults import OUTPUT_LEVELS

    words = {"json", *OUTPUT_LEVELS}
    return sorted(
        f"{frontend}:{name}"
        for frontend, plugins in frontend_plugins.items()
        for name in plugins
        if "." in name and name.rpartition(".")[2] in words
    )


@pytest.mark.rule("suffix-words-reserved")
def test_no_command_name_ends_in_a_suffix_word(frontend_plugins):
    """No command in any frontend ends its name in .json or a level word, so the suffix means one thing on every command."""
    # Arrange / Act
    taken = _names_on_suffix_words(frontend_plugins)

    # Assert
    assert taken == [], (
        "a name ending in .json/.silent/.quiet/.normal/.verbose takes the per-call suffix "
        "away from its family (ReplEngine.split_suffixes); give the behavior a parameter "
        f"or a level instead: {taken}"
    )


def test_suffix_word_guard_fires_on_a_probe():
    """The guard flags a dotted name ending in a suffix word and passes a plain or undotted one."""
    # Arrange
    plugins = {"probe": {"ping.quiet": None, "term.json_fmt": None, "verbose": None, "a.b": None}}

    # Act
    taken = _names_on_suffix_words(plugins)

    # Assert
    assert taken == ["probe:ping.quiet"], "only the dotted name ending in a suffix word"


def _docstring_of(handler) -> str:
    """The docstring ``/help.dev`` would print for ``handler``, or ``""``."""
    return (getattr(handler, "__doc__", None) or "").strip()


@pytest.mark.rule("handler-docstring")
def test_every_registered_handler_has_a_docstring(frontend_plugins):
    """Every command's handler carries a docstring in every frontend, so /help.dev and developer search have text for it."""
    # Arrange / Act
    undocumented = sorted(
        f"{frontend}:{name}"
        for frontend, plugins in frontend_plugins.items()
        for name, info in plugins.items()
        if not _docstring_of(info.handler)
    )
    # Assert
    assert undocumented == [], (
        "a handler without a docstring answers /help.dev with '(no docstring)'; a factory "
        f"stamps one per command, a TUI hook goes through app_hooks._bind: {undocumented}"
    )


@pytest.mark.rule("handler-docstring")
def test_every_example_plugin_handler_has_a_docstring():
    """The example plugins, which no frontend registers, document every handler a plugin author copies."""
    import ast

    # Arrange / Act
    undocumented = sorted(
        f"{path.name}:{node.name}"
        for path in (TESTS.parent / "examples" / "plugins").glob("*.py")
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_handler")
        and not ast.get_docstring(node)
    )
    # Assert
    assert undocumented == [], (
        f"an example handler is copied by plugin authors; document it: {undocumented}"
    )


@pytest.mark.rule("handler-docstring")
def test_handler_docstring_guard_flags_a_bare_handler():
    """The guard's predicate rejects a lambda and an undocumented def, and accepts a documented one."""

    def documented(ctx, args):
        """Does a thing."""

    def bare(ctx, args):
        return None

    assert _docstring_of(lambda ctx, args: None) == "", "a lambda has no docstring"
    assert _docstring_of(bare) == "", "an undocumented def has none"
    assert _docstring_of(documented) == "Does a thing.", "a documented handler passes"


# ── A handler declares -> CmdResult (registry handler-returns-cmdresult) ─────
#
# Dispatch fails a handler that returns anything but a CmdResult
# (tests/test_handler_returns.py); declaring it makes ty check every return
# path, so a forgotten ``return`` is caught before it runs.  Until 2026-11-06
# dispatch turned a handler's None into an empty success, which no built-in
# relied on and which hid exactly that bug.


def _declares_cmdresult(handler) -> bool:
    """Whether ``handler`` declares ``-> CmdResult``: a string under postponed annotations, else the class."""
    import inspect

    try:
        annotation = inspect.signature(handler).return_annotation
    except (TypeError, ValueError):
        return False
    return annotation == "CmdResult" or getattr(annotation, "__name__", "") == "CmdResult"


@pytest.mark.rule("handler-returns-cmdresult")
def test_every_registered_handler_declares_cmdresult(frontend_plugins):
    """Every command's handler declares -> CmdResult in every frontend, so ty checks each return path."""
    # Arrange / Act
    undeclared = sorted(
        f"{frontend}:{name}"
        for frontend, plugins in frontend_plugins.items()
        for name, info in plugins.items()
        if not _declares_cmdresult(info.handler)
    )
    # Assert
    assert undeclared == [], (
        "annotate the handler (ctx: PluginContext, args: str) -> CmdResult; a factory's "
        f"inner handler too: {undeclared}"
    )


@pytest.mark.rule("handler-returns-cmdresult")
def test_every_example_plugin_handler_declares_cmdresult():
    """The example plugins, which no frontend registers, declare -> CmdResult on every handler."""
    import ast

    # Arrange / Act
    undeclared = sorted(
        f"{path.name}:{node.name}"
        for path in (TESTS.parent / "examples" / "plugins").glob("*.py")
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_handler")
        and not (isinstance(node.returns, ast.Name) and node.returns.id == "CmdResult")
    )
    # Assert
    assert undeclared == [], f"an example handler is copied by plugin authors: {undeclared}"


@pytest.mark.rule("handler-returns-cmdresult")
def test_handler_return_guard_fires_on_a_probe():
    """The predicate rejects no annotation and -> None, and accepts -> CmdResult."""
    from termapy.plugins import CmdResult

    def bare(ctx, args):
        """No annotation."""

    def returns_none(ctx, args) -> None:
        """Declares None."""

    def declared(ctx, args) -> CmdResult:
        """Declares the contract."""
        return CmdResult.ok(value="")

    actual = [_declares_cmdresult(handler) for handler in (bare, returns_none, declared)]
    assert actual == [False, False, True], "only -> CmdResult passes"


@pytest.mark.rule("one-handler-per-command")
def test_each_command_has_one_handler_in_every_frontend(frontend_tables):
    """No name runs different handlers in two frontends, outside the allowlist."""
    # Arrange / Act
    divergent = {
        name: frontends
        for name, frontends in _divergent_commands(frontend_tables).items()
        if name not in KNOWN_DIVERGENT_COMMANDS
    }

    # Assert
    assert divergent == {}, (
        "a frontend registers its own handler for a command another frontend has; "
        "change HOW through a host method or a handle field instead:\n  "
        + "\n  ".join(f"/{name}: {', '.join(frontends)}" for name, frontends in divergent.items())
    )


def test_one_handler_guard_fires_on_a_probe():
    """The guard flags a name whose handlers differ and passes a shared or one-frontend name."""

    # Arrange
    def shared(ctx, args):
        return None

    def tui_own(ctx, args):
        return None

    def cli_own(ctx, args):
        return None

    tables = {
        "TUI": {
            "same": _handler_identity(shared),
            "split": _handler_identity(tui_own),
            "tui_only": 1,
        },
        "CLI": {"same": _handler_identity(shared), "split": _handler_identity(cli_own)},
    }

    # Act
    actual = _divergent_commands(tables)

    # Assert
    assert actual == {"split": ["CLI", "TUI"]}, "only the name with two handlers"


def test_known_divergent_commands_has_no_stale_entries(frontend_tables):
    """Every allowlisted name still differs between frontends, so the list only shrinks."""
    # Arrange / Act
    divergent = _divergent_commands(frontend_tables)

    # Assert
    stale = sorted(set(KNOWN_DIVERGENT_COMMANDS) - set(divergent))
    assert stale == [], f"one handler everywhere now; drop from KNOWN_DIVERGENT_COMMANDS: {stale}"


# ── /help AVAILABLE says where a command is registered ───────────────────────
#
# Review 2026-10-15 (D8): /help computes its AVAILABLE row from a command's
# ``needs=`` against ``ENVIRONMENTS``, so the row is right only while every
# frontend whose capabilities satisfy a command also registers it.
# ``/help run.profile`` said "MCP: yes" for a hook only the TUI and the CLI
# registered; ``/clr`` was the CLI's alone.  This builds the three tables and
# fails on a frontend that lacks a command its row claims.  The allowlist
# only shrinks.
KNOWN_UNREGISTERED_WHERE_AVAILABLE: dict[str, str] = {
    "cli.completion": "the CLI prompt's own setting; no capability names the CLI prompt, so the row says TUI: yes",
}


def _claimed_but_unregistered(
    plugins_by_frontend: dict[str, dict[str, Any]],
    environments: dict[str, Any],
) -> dict[str, list[str]]:
    """``{command name: [frontends]}`` where a frontend's /help row says yes and that frontend has no such command."""
    claimed: dict[str, set[str]] = {}
    for plugins in plugins_by_frontend.values():
        for name, info in plugins.items():
            for env_name, env_caps in environments.items():
                if env_name in plugins_by_frontend and not info.needs.missing_from(env_caps):
                    if name not in plugins_by_frontend[env_name]:
                        claimed.setdefault(name, set()).add(env_name)
    return {name: sorted(frontends) for name, frontends in sorted(claimed.items())}


@pytest.mark.rule("available-matches-registration")
def test_help_available_names_only_frontends_that_register_the_command(frontend_plugins):
    """Every frontend /help AVAILABLE says yes for registers the command."""
    from termapy.plugins.capabilities import ENVIRONMENTS

    # Arrange / Act
    unregistered = {
        name: frontends
        for name, frontends in _claimed_but_unregistered(frontend_plugins, ENVIRONMENTS).items()
        if name not in KNOWN_UNREGISTERED_WHERE_AVAILABLE
    }

    # Assert
    assert unregistered == {}, (
        "/help AVAILABLE says yes where the command is not registered; register it there "
        "or declare the capability it really needs:\n  "
        + "\n  ".join(
            f"/{name}: {', '.join(frontends)}" for name, frontends in unregistered.items()
        )
    )


def test_available_guard_fires_on_a_probe():
    """The guard flags a command whose needs a frontend meets but which that frontend lacks, and passes a gated one."""
    from types import SimpleNamespace

    from termapy.plugins import CapabilitySet

    # Arrange -- "both" needs interactive, which both frontends have; "tui" needs tui_mode
    environments = {
        "TUI": CapabilitySet(interactive=True, tui_mode=True),
        "CLI": CapabilitySet(interactive=True),
    }
    both = SimpleNamespace(needs=CapabilitySet.INTERACTIVE)
    tui = SimpleNamespace(needs=CapabilitySet.TUI_MODE)
    plugins = {"TUI": {"both": both, "tui": tui}, "CLI": {}}

    # Act
    actual = _claimed_but_unregistered(plugins, environments)

    # Assert
    assert actual == {"both": ["CLI"]}, "only the command the CLI qualifies for and lacks"


def test_known_unregistered_where_available_has_no_stale_entries(frontend_plugins):
    """Every allowlisted name still makes a false AVAILABLE claim, so the list only shrinks."""
    from termapy.plugins.capabilities import ENVIRONMENTS

    # Arrange / Act
    unregistered = _claimed_but_unregistered(frontend_plugins, ENVIRONMENTS)

    # Assert
    stale = sorted(set(KNOWN_UNREGISTERED_WHERE_AVAILABLE) - set(unregistered))
    assert stale == [], (
        f"/help tells the truth now; drop from KNOWN_UNREGISTERED_WHERE_AVAILABLE: {stale}"
    )


# ── Text files are UTF-8, never UTF-8 read back as cp1252 ─────────────────────
#
# Bug 2026-10-17: an edit in 72a671e round-tripped app.py through cp1252, so
# each non-ASCII character was saved as the cp1252 reading of its UTF-8 bytes,
# and the find bar's prev / next / close buttons showed gibberish for a month
# with every test green.  That reading has one shape -- a lead letter
# (U+00C2..U+00F4) and then the characters cp1252 shows for the continuation
# bytes, which encode back to valid UTF-8 -- and real text almost never has
# it, so the guard can be exact rather than a heuristic.
MOJIBAKE_ROOTS = ("src/termapy", "tests", "docs", "scripts", "examples")
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".cfg", ".run", ".pro", ".json", ".toml", ".txt", ".tcss", ".html", ".expected"}
)


def _cp1252_char(byte: int) -> str:
    """The character a cp1252 reading shows for ``byte``; latin-1 where cp1252 leaves it undefined."""
    try:
        return bytes([byte]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(byte)


_CP1252_BYTE = {_cp1252_char(byte): byte for byte in range(0x80, 0x100)}
_CONTINUATION = "[" + "".join(re.escape(_cp1252_char(byte)) for byte in range(0x80, 0xC0)) + "]"
_MOJIBAKE = re.compile(f"[Â-ß]{_CONTINUATION}|[à-ï]{_CONTINUATION}{{2}}|[ð-ô]{_CONTINUATION}{{3}}")


def _restore(run: str) -> str:
    """The character ``run`` is the cp1252 reading of; UnicodeDecodeError when it is none."""
    return bytes(_CP1252_BYTE[char] for char in run).decode("utf-8")


def _mojibake(text: str) -> list[str]:
    """Each run in ``text`` that is one UTF-8 character read as cp1252, in order."""
    runs: list[str] = []
    for match in _MOJIBAKE.finditer(text):
        try:
            _restore(match.group())
        except UnicodeDecodeError:
            continue
        runs.append(match.group())
    return runs


def _text_files() -> list[Path]:
    """The repo-root Markdown and every text file under ``MOJIBAKE_ROOTS``."""
    repo = SRC.parent.parent
    files = list(repo.glob("*.md"))
    for root in MOJIBAKE_ROOTS:
        files.extend(
            path
            for path in (repo / root).rglob("*")
            if path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts
        )
    return sorted(files)


@pytest.mark.rule("no-mojibake")
def test_text_files_are_never_utf8_read_as_cp1252():
    """Every text file is UTF-8 and holds no UTF-8 character read back as cp1252."""
    # Arrange
    repo = SRC.parent.parent
    violations: list[str] = []

    # Act
    for path in _text_files():
        rel = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            violations.append(f"{rel}: not UTF-8 ({e.reason} at byte {e.start})")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if runs := dict.fromkeys(_mojibake(line)):
                names = ", ".join(unicodedata.name(_restore(run), "?") for run in runs)
                violations.append(f"{rel}:{number}: should be {names}")

    # Assert
    assert violations == [], (
        "a tool read these files as cp1252 and wrote them back; restore each "
        "garbled run to the character named:\n  " + "\n  ".join(violations)
    )


def test_mojibake_guard_fires_on_a_probe():
    """The guard flags 2-, 3- and 4-byte characters read as cp1252, and passes the real characters and Latin-1 prose."""
    # Arrange -- garbled at run time, so this file never holds the shapes it hunts
    close, up, rule, smile = (char.encode().decode("cp1252") for char in ("×", "▲", "─", "😀"))
    source = (
        f'close = Button("{close}")\n'
        f'prev = Button("{up}")\n'
        f"# {rule * 3} header\n"
        f'face = "{smile}"\n'
        'clean = "× ▲ ─ 😀 café naïve Ångström 25°C ±1"\n'
    )

    # Act
    actual = _mojibake(source)

    # Assert
    expected = [close, up, rule, rule, rule, smile]
    assert actual == expected, "each garbled character once; never the real ones or accented prose"


# ── prefix-never-hardcoded ─────────────────────────────────────────────────────

# Files allowed to spell "/<command>" in a non-docstring string, each with the
# reason it is legitimately literal.  The list only shrinks (see the stale-entry
# test); the fix for a new hit is {prefix} or ctx.prefix, never a new entry.
ALLOWED_PREFIX_SPELLINGS: dict[str, str] = {
    "termapy/builtins/commands/credits.py": (
        "the acknowledgments page, generated into help/, where documentation shows the default prefix"
    ),
    "termapy/credits_data.py": "Credit notes feed that same generated page",
    "termapy/demo.py": "the DEMO device's own help text is device output, not termapy's",
    "termapy/entry.py": "argparse help, shown before any config exists; only the default prefix applies there",
    "termapy/migration.py": "translates files written when / was the only prefix, and quotes them back",
}

_DISPATCH_FUNCS: frozenset[str] = frozenset(
    {
        "dispatch",
        "dispatch_full",
        "_dispatch_quiet",
        "_dispatch_single",
        "_dispatch_on_thread",
        "_dispatch_on_thread_interactive",
        "_prompt_then_dispatch",
    }
)
_HOOK_SOURCES = ("termapy/app.py", "termapy/cli.py", "termapy/app_hooks.py")
# A slash, a command token, an optional dotted tail; not inside a path (/dev/tty) or a word.
_SPELLING = re.compile(r"(?<![\w/])/([a-z][a-z0-9_]*)(?:\.[a-z0-9_]+)*(?![\w/])")


def _command_tokens() -> frozenset[str]:
    """Top-level names of every built-in command plus every host-registered hook."""
    from termapy.plugins import builtins_dir, load_plugins_from_dir

    tokens = {
        info.name.split(".")[0]
        for info in load_plugins_from_dir(builtins_dir(), source="built-in").plugins
    }
    for rel in _HOOK_SOURCES:
        tree = ast.parse((SRC.parent / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _call_name(node).endswith("register_hook")
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    tokens.add(first.value.split(".")[0])
    return frozenset(tokens)


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _documentation_strings(tree: ast.AST) -> set[int]:
    """ids of every statement-level string: docstrings and attribute docs, never rendered."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _leading_literal(node: ast.AST) -> str | None:
    """The literal text an argument starts with: a plain string, or an f-string's first piece."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value)
    return None


def _dispatch_literals(tree: ast.AST, rel: str) -> list[str]:
    """Every dispatch call handed a string that starts with a typed slash."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in _DISPATCH_FUNCS:
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                text = _leading_literal(arg)
                if text is not None and text.startswith("/"):
                    found.append(f"{rel}:{node.lineno}  {_call_name(node)}({text!r})")
    return found


def _prefix_spellings(tree: ast.AST, rel: str, tokens: frozenset[str]) -> list[str]:
    """Every non-documentation string constant that spells a command with a slash."""
    docs = _documentation_strings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            hits = [m.group(0) for m in _SPELLING.finditer(node.value) if m.group(1) in tokens]
            if hits:
                found.append(f"{rel}:{node.lineno}  {', '.join(hits)}")
    return found


@pytest.mark.rule("prefix-never-hardcoded")
def test_dispatch_lines_use_the_live_prefix():
    """A line handed to dispatch is built from the config's prefix, never typed as /cmd.

    With ``cmd_prefix: "!"`` a typed ``"/find.prev"`` is not a command but
    device text, so every button, menu action and palette entry that
    spelled the slash sent its command to the wire (nineteen sites,
    2026-10-24).  A synthesized line goes through ``cmd_prefix(cfg)`` or
    ``ctx.prefix``; the test has no allowlist because no dispatch literal
    is ever legitimate.
    """
    # Act
    violations = [
        hit
        for path in _package_python_files()
        for hit in _dispatch_literals(
            ast.parse(path.read_text(encoding="utf-8")), path.relative_to(SRC.parent).as_posix()
        )
    ]

    # Assert
    assert violations == [], (
        "a dispatched line is built from the live prefix (app._repl_line, ctx.prefix), never typed:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("prefix-never-hardcoded")
def test_command_spellings_use_the_prefix_placeholder():
    """Help and message text writes {prefix}cmd, which every renderer fills in; docs alone show /.

    ``interpolate_help`` substitutes the placeholder in ``help`` and
    ``long_help``, the MCP catalog and the config editor; a message built in
    a handler uses ``ctx.prefix``, the engine ``self.prefix``, a gate message
    ``state_message``.  Docstrings, comments and attribute docs are not
    rendered and are not checked; the Markdown help pages are documentation
    and show the default on purpose.
    """
    # Arrange
    tokens = _command_tokens()

    # Act
    violations = [
        hit
        for path in _package_python_files()
        if path.relative_to(SRC.parent).as_posix() not in ALLOWED_PREFIX_SPELLINGS
        for hit in _prefix_spellings(
            ast.parse(path.read_text(encoding="utf-8")),
            path.relative_to(SRC.parent).as_posix(),
            tokens,
        )
    ]

    # Assert
    assert violations == [], (
        "a command in rendered text is {prefix}cmd (or ctx.prefix in a message), never /cmd:\n  "
        + "\n  ".join(violations)
        + "\n\nOnly text that is literal by nature (see ALLOWED_PREFIX_SPELLINGS) may keep the slash."
    )


def test_prefix_guard_fires_on_a_probe():
    """Both scans flag a typed slash and pass the placeholder, a device path and a docstring."""
    # Arrange
    probe = (
        '"""Module doc mentions /cfg and is fine."""\n'
        'self._dispatch_quiet("/find.prev")\n'
        'self._dispatch_quiet(self._repl_line("find.prev"))\n'
        'self._dispatch_on_thread(f"/run.record {name}")\n'
        'help_text = "See /cfg.auto for details"\n'
        'placeholder = "See {prefix}cfg.auto for details"\n'
        'port = "/dev/ttyUSB0"\n'
        "X = 1\n"
        '"""Attribute doc: /log commands need a config."""\n'
    )
    tree = ast.parse(probe)
    tokens = frozenset({"find", "run", "cfg", "dev", "log"})

    # Act
    dispatch_hits = _dispatch_literals(tree, "probe")
    spelling_hits = _prefix_spellings(tree, "probe", tokens)

    # Assert
    assert sorted(hit.split("  ")[0] for hit in dispatch_hits) == ["probe:2", "probe:4"], (
        "the two typed dispatch lines are flagged; the _repl_line one is not"
    )
    assert sorted(hit.split("  ")[0] for hit in spelling_hits) == [
        "probe:2",
        "probe:4",
        "probe:5",
    ], (
        "the typed help string is flagged; the placeholder, the device path and both docstrings are not"
    )


def test_prefix_allowlist_has_no_stale_entries():
    """Every allowlisted file still spells a command; one that was cleaned up comes off the list."""
    # Arrange
    tokens = _command_tokens()

    # Act
    stale = [
        rel
        for rel in ALLOWED_PREFIX_SPELLINGS
        if not _prefix_spellings(
            ast.parse((SRC.parent / rel).read_text(encoding="utf-8")), rel, tokens
        )
    ]

    # Assert
    assert stale == [], f"no longer spell a command; remove from ALLOWED_PREFIX_SPELLINGS: {stale}"


# ── help-group-declared ───────────────────────────────────────────────────────
#
# A command declares what it is FOR, and /help lays the landscape out from
# the declarations.  The guards below pin the three halves of that: every
# visible root declares a group, a family shares its root's, and the
# landscape never reaches for where a command came from.


@pytest.mark.rule("help-group-declared")
def test_every_visible_root_declares_a_group(frontend_plugins):
    """Every root a user can see declares a group, not the default."""
    # Arrange
    from termapy.plugins import GROUP_DEFAULT

    # Act
    undeclared = {
        frontend: sorted(
            name
            for name, info in plugins.items()
            if "." not in name and info.group == GROUP_DEFAULT
        )
        for frontend, plugins in frontend_plugins.items()
    }
    missing = {frontend: names for frontend, names in undeclared.items() if names}

    # Assert
    assert missing == {}, (
        "a visible top-level command declares no group, so /help folds it under "
        f"{GROUP_DEFAULT!r} with unrelated commands; declare group= on its COMMAND:\n  "
        + "\n  ".join(f"{frontend}: {names}" for frontend, names in missing.items())
    )


@pytest.mark.rule("help-group-declared")
def test_subcommands_inherit_their_roots_group(frontend_plugins):
    """A family shares one group -- a subcommand cannot leave its root's."""
    # Arrange / Act
    strays = []
    for frontend, plugins in frontend_plugins.items():
        for name, info in plugins.items():
            if "." not in name:
                continue
            root = plugins.get(name.split(".", 1)[0])
            if root is not None and info.group != root.group:
                strays.append(
                    f"{frontend}: /{name} is {info.group!r}, /{root.name} is {root.group!r}"
                )

    # Assert
    assert strays == [], "a subcommand left its root's group:\n  " + "\n  ".join(strays)


@pytest.mark.rule("help-group-declared")
def test_the_landscape_never_groups_by_source(frontend_plugins):
    """/help's landscape reads group, never source -- origin is not privilege."""
    # Arrange
    import inspect
    import textwrap

    import termapy.builtins.commands.help as help_mod

    source = inspect.getsource(help_mod._handler)
    tree = ast.parse(textwrap.dedent(source))

    # Act -- any read of ``.source`` on a plugin in the landscape handler
    reads = [
        node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "source"
    ]

    # Assert
    assert reads == [], (
        "the landscape reads a command's source; it must lay out by group so a user "
        "plugin declaring 'terminal' sits among the built-ins that did"
    )


@pytest.mark.rule("help-group-declared")
def test_a_user_plugin_joins_the_terminal_group(tmp_path):
    """A loaded plugin declaring terminal is in it, as any built-in is."""
    # Arrange -- a user plugin that claims the unfolded group
    folder = tmp_path / "plugin"
    folder.mkdir()
    (folder / "mine.py").write_text(
        "from termapy.plugins import CmdResult, Command\n"
        "def _handler(ctx, args):\n"
        "    return CmdResult.ok(value='')\n"
        "COMMAND = Command(\n"
        "    group='terminal',\n"
        "    name='mine',\n"
        "    help='A command of my own.',\n"
        "    handler=_handler,\n"
        ")\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "bare.cfg"
    cfg.write_text("{}", encoding="utf-8")

    # Act -- resolve_plugins is the step that reads the folders
    from termapy.plugins import GROUP_TERMINAL
    from termapy.repl import ReplEngine

    engine = ReplEngine({}, str(cfg), lambda *a, **k: None, global_root=tmp_path)
    engine.resolve_plugins()
    mine = engine._plugins.get("mine")

    # Assert
    assert mine is not None, "the plugin loaded"
    assert mine.group == GROUP_TERMINAL, (
        "a user plugin that declares the terminal group is in it; nothing about where "
        "a command came from decides where /help shows it"
    )


@pytest.mark.rule("help-group-declared")
def test_group_guard_fires_on_a_probe(frontend_plugins):
    """The declaration guard reddens for a root left at the default."""
    # Arrange -- a root carrying the default group, as an undeclared command would
    from dataclasses import replace

    from termapy.plugins import GROUP_DEFAULT

    plugins = dict(next(iter(frontend_plugins.values())))
    probe = replace(plugins["help"], name="probe", group=GROUP_DEFAULT)

    # Act
    undeclared = [
        name
        for name, info in {**plugins, "probe": probe}.items()
        if "." not in name and info.group == GROUP_DEFAULT
    ]

    # Assert
    assert undeclared == ["probe"], "the probe is the one undeclared root the guard would name"


# ── compact-input-variant ─────────────────────────────────────────────────────
#
# A one-row text field is Textual's compact Input variant, asked for by
# name.  Five fields across three dialogs hand-rolled it in CSS instead
# (``height: 1; border: none``), which is what the variant already is --
# minus its focus tint and its invalid-state tint, and with the styling
# ours to maintain rather than Textual's.


@pytest.mark.rule("compact-input-variant")
def test_a_one_row_input_asks_for_the_compact_variant():
    """No CSS hand-rolls what Input(compact=True) already is.

    The fix for a red result is ``compact=True`` on the Input and the
    width-only CSS rule, never a new hand-rolled rule: a one-row field
    should be the same control on every screen.
    """
    # Arrange -- the UI layer's stylesheets live in these modules
    import re

    ui_files = sorted((SRC / "dialogs").glob("*.py")) + [SRC / "proto_debug.py"]
    # ``height: 1`` and ``border: none`` on one selector is the variant,
    # spelled out by hand.
    hand_rolled = re.compile(r"#[\w-]+\s*\{\{[^}]*height:\s*1;[^}]*border:\s*none;[^}]*\}\}")

    # Act
    offenders = [
        f"{path.relative_to(SRC.parent)}: {match.group(0).strip()}"
        for path in ui_files
        for match in hand_rolled.finditer(path.read_text(encoding="utf-8"))
    ]

    # Assert
    assert offenders == [], (
        "these spell out Textual's compact Input variant in CSS; pass "
        "compact=True to the Input instead and leave the rule to the width:\n  "
        + "\n  ".join(offenders)
    )


# ── Config keys: a command declares what it reads ───────────────────────────
#
# A command declares the config keys it depends on and how (Command.cfg);
# /help, /search, the config editor and the MCP catalog derive their answers
# from that.  The declaration is honest only if every key a plugin module
# reads is one some command in it declares, so this walk finds the literal
# reads and session sets a handler or its helpers make and holds them to the
# module's declarations.  The scope is the command module plus the _helper
# modules it imports: a helper's reads belong to the commands that import it.

# The keys whose value is a dict: a read of one of these on its own is
# navigation (cfg.get("serial", {})); the leaf read on it is the read.
_CFG_DICT_KEYS = frozenset(key for key in cfg_key_paths() if isinstance(cfg_value({}, key), dict))


def _is_cfg_receiver(node: ast.AST) -> bool:
    """``cfg``, ``ctx.cfg``, ``self._cfg_data``, ``get_cfg()``: an expression that is the config dict."""
    if isinstance(node, ast.Name):
        return node.id == "cfg"
    if isinstance(node, ast.Call):
        return isinstance(node.func, ast.Name) and node.func.id == "get_cfg"
    return isinstance(node, ast.Attribute) and node.attr in ("cfg", "_cfg", "_cfg_data")


def _literal_key(node: ast.AST) -> tuple[ast.AST, str] | None:
    """``(receiver, key)`` for ``X.get("key", ...)`` or ``X["key"]``; ``None`` for any other node."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.func.value, node.args[0].value
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.value, node.slice.value
    return None


def _cfg_read_path(node: ast.AST) -> str | None:
    """The key a ``cfg.get("k")`` / ``cfg["k"]`` / ``cfg["serial"]["k"]`` node reads; ``None`` when it is not one."""
    found = _literal_key(node)
    if found is None:
        return None
    receiver, key = found
    if key.startswith("_"):
        return None  # a session marker (_config_path, _config_warnings), never a config key
    if _is_cfg_receiver(receiver):
        return None if key in _CFG_DICT_KEYS else key
    inner = _literal_key(receiver)
    if inner is not None and inner[1] in _CFG_DICT_KEYS and _is_cfg_receiver(inner[0]):
        return f"{inner[1]}.{key}"
    return None


def _str_arg(node: ast.Call, index: int) -> str | None:
    """The string literal at positional ``index`` of a call, else ``None``."""
    if len(node.args) > index:
        arg = node.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def _cfg_literal_reads(tree: ast.Module) -> list[tuple[str, str, int]]:
    """``(qualname, key, line)`` of every literal config read or session set in ``tree``.

    The shapes: ``cfg.get("k")`` / ``cfg["k"]`` on a config receiver, with
    ``["serial"]["k"]`` read as ``serial.k``; ``_cfg_toggle(ctx, args, "k")``;
    ``apply_cfg("k", v)``; ``cfg_value(cfg, "k")``; a ``{"cfg_update":
    {"k": v}}`` effect; and ``update_port(name)``, which sets ``serial.port``.
    A key is as written (a flat leaf stays a leaf); the check resolves it.
    """
    sites: list[tuple[str, str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        line = getattr(node, "lineno", 0)
        path = _cfg_read_path(node)
        if path is not None:
            sites.append((qualname, path, line))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            key = None
            if name == "_cfg_toggle":
                key = _str_arg(node, 2)
            elif name == "apply_cfg":
                key = _str_arg(node, 0)
            elif name == "cfg_value":
                key = _str_arg(node, 1)
            elif name == "update_port":
                key = "serial.port"
            if key is not None:
                sites.append((qualname, key, line))
        elif isinstance(node, ast.Dict):
            for dict_key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(dict_key, ast.Constant)
                    and dict_key.value == "cfg_update"
                    and isinstance(value, ast.Dict)
                ):
                    sites.extend(
                        (qualname, inner.value, line)
                        for inner in value.keys
                        if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
                    )
    return sites


def _cfg_declarations(tree: ast.Module) -> set[str]:
    """Every key path a ``Command(... cfg={...})`` in ``tree`` declares, a leaf resolved to its path."""
    declared: set[str] = set()
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Command"
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg == "cfg" and isinstance(keyword.value, ast.Dict):
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        declared.add(cfg_key_path(key.value) or key.value)
    return declared


def _plugin_scope(module):
    """A command module's tree and those of the ``_helper`` siblings it imports: one declaration scope."""
    tree = ast.parse(module.read_text(encoding="utf-8"))
    scope = [(module, tree)]
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "termapy.builtins.commands._"
        ):
            helper = module.parent / f"{(node.module or '').rpartition('.')[2]}.py"
            if helper.exists():
                scope.append((helper, ast.parse(helper.read_text(encoding="utf-8"))))
    return scope


def _undeclared_cfg_reads() -> list[str]:
    """Every literal config read in the plugin folders that no command in its module declares."""
    root = SRC.parent.parent
    violations: list[str] = []
    for folder in PLUGIN_FOLDERS:
        for module in sorted(folder.glob("*.py")):
            if module.name.startswith("_"):
                continue  # a helper's reads are checked with the command module that imports it
            scope = _plugin_scope(module)
            declared = set().union(*(_cfg_declarations(tree) for _, tree in scope))
            for path, tree in scope:
                for qualname, key, line in _cfg_literal_reads(tree):
                    if (cfg_key_path(key) or key) not in declared:
                        rel = path.relative_to(root).as_posix()
                        violations.append(f"{rel}:{line} {qualname} reads {key!r}")
    return violations


@pytest.mark.rule("cfg-declared")
def test_plugin_cfg_reads_are_declared():
    """Every config key a plugin module reads or sets is one a command in it declares, with how.

    The fix for a red result is ``cfg={"<key>": "<how this command uses
    it>"}`` on the command that reads it, never a read that hides the key
    behind a variable.
    """
    # Arrange / Act
    violations = _undeclared_cfg_reads()

    # Assert
    assert violations == [], (
        "declare the key on the command that reads it, cfg={'key': 'how it is used'}:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("cfg-declared")
def test_cfg_read_guard_fires_on_a_probe():
    """The read finder sees every shape, skips navigation and a variable key, and the declaration finder resolves a leaf."""
    # Arrange -- line 3 on: each read shape once, then navigation, then a variable key
    source = """
def reads(ctx, args, cfg, key):
    ctx.cfg.get("encoding", "utf-8")
    ctx.cfg["serial"]["port"]
    cfg.get("serial", {}).get("baud_rate")
    ctx.cfg.get("serial", {})
    ctx.cfg.get(key)
    _cfg_toggle(ctx, args, "timestamps")
    ctx.internal.apply_cfg("eol", "\\n")
    cfg_value(ctx.cfg, "title")
    ctx.serial.apply_port_effects({"cfg_update": {"parity": "N", "echo": True}})
    ctx.serial.update_port("COM3")

COMMAND = Command(name="x", help="h", handler=reads, cfg={"baud_rate": "set", "eol": "sent"})
"""
    tree = ast.parse(source)

    # Act
    reads = sorted(
        ((key, line) for _, key, line in _cfg_literal_reads(tree)), key=lambda item: item[1]
    )
    declared = _cfg_declarations(tree)

    # Assert
    expected = [
        ("encoding", 3),
        ("serial.port", 4),
        ("serial.baud_rate", 5),
        ("timestamps", 8),
        ("eol", 9),
        ("title", 10),
        ("parity", 11),
        ("echo", 11),
        ("serial.port", 12),
    ]
    assert reads == expected, (
        "every shape once; navigation (line 6) and a variable key (line 7) are not reads"
    )
    assert declared == {"serial.baud_rate", "eol"}, "a leaf is stored as its path"


# ── Config values: one reader with the table's default ───────────────────────
#
# A config value with a default is read through defaults.cfg_value, so the
# default is DEFAULT_CFG's and lives in one place: ~148 sites restated it in
# a literal, five of them differently from the table (decision 2026-11-01).
# A subscript (cfg["eol"]) stays allowed: it is loud when the key is missing.
# The raw-file readers in config.py see a file as written, where a missing
# key must stay missing, and are allowlisted with that reason; migration.py's
# literal defaults are frozen history and defaults.py is the table itself.

CFG_GET_ALLOWED: dict[tuple[str, str], str] = {
    ("config.py", "validate_config"): "reads the file as written: a missing key is a finding",
    ("config.py", "load_config"): "reads the file as written: a missing config_version is 0",
    ("config.py", "config_summary"): "summarizes a file as written, before any backfill",
}

# Modules the reader rule does not reach: the table, and the frozen history.
_CFG_GET_EXEMPT_MODULES = frozenset({"defaults.py", "migration.py"})


def _cfg_get_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every ``cfg.get("k", ...)`` on a config receiver, ``["serial"].get("k")`` included."""
    return [
        (qualname, node.lineno)
        for qualname, node in _scoped_nodes(tree)
        if isinstance(node, ast.Call) and _cfg_read_path(node) is not None
    ]


def _unallowed_cfg_gets() -> tuple[list[str], set[tuple[str, str]]]:
    """Sites outside the allowlist, and the allowlist entries that matched something."""
    violations: list[str] = []
    used: set[tuple[str, str]] = set()
    for rel, tree in _source_modules():
        if rel in _CFG_GET_EXEMPT_MODULES:
            continue
        for qualname, line in _cfg_get_sites(tree):
            if (rel, qualname) in CFG_GET_ALLOWED:
                used.add((rel, qualname))
            else:
                violations.append(f"{rel}:{line} in {qualname}")
    return violations, used


@pytest.mark.rule("cfg-read-one-reader")
def test_core_reads_cfg_values_through_cfg_value():
    """A config value with a default is read through defaults.cfg_value, never cfg.get("k", literal).

    The fix for a red result is ``cfg_value(cfg, "key")`` (a nested key as
    its path, ``"serial.port"``), which falls back to the table's default;
    a reader of a file as written is allowlisted with that reason.
    """
    # Arrange / Act
    violations, _ = _unallowed_cfg_gets()

    # Assert
    assert violations == [], (
        "read the value with defaults.cfg_value(cfg, 'key'), or allowlist the function "
        "with the reason a missing key must stay missing:\n  " + "\n  ".join(violations)
    )


@pytest.mark.rule("cfg-read-one-reader")
def test_cfg_value_guard_fires_on_a_probe():
    """The finder flags a literal get on a config receiver, chained through serial too, and passes a subscript, navigation, cfg_value and another dict."""
    # Arrange
    source = """
def reads(ctx, cfg, other, get_cfg):
    ctx.cfg.get("eol", "\\r")
    cfg.get("serial", {}).get("port")
    get_cfg().get("title")
    cfg["eol"]
    cfg.get("serial", {})
    cfg_value(cfg, "eol")
    other.get("eol", "x")
    cfg.get("_config_path")
"""
    tree = ast.parse(source)

    # Act
    lines = [line for _, line in _cfg_get_sites(tree)]

    # Assert
    assert lines == [3, 4, 5], (
        "the three literal gets; not the subscript, navigation, cfg_value, another dict or a session marker"
    )


@pytest.mark.rule("cfg-read-one-reader")
def test_cfg_get_allowlist_has_no_stale_entries():
    """Every allowlisted function still reads a config value by literal get, so the allowlist only shrinks."""
    # Arrange / Act
    _, used = _unallowed_cfg_gets()

    # Assert
    stale = sorted(set(CFG_GET_ALLOWED) - used)
    assert stale == [], f"allowlisted but no longer a literal cfg.get site; drop them: {stale}"


# ── config-load-deterministic: module-level state ─────────────────────────────

# A module whose functions write module-level state, with why that state is not in
# ``ctx.ns()``, which a load empties with no hook.  Only shrinks.
MODULE_STATE_OUTSIDE_NS = {
    "breadcrumbs.py": "the process's own breadcrumb file, which no config owns",
    "builtins/commands/_run_record.py": (
        "an open file and an observer a cleared dict cannot release; "
        "run.py's on_config_load stops the recording"
    ),
    "builtins/commands/run.py": (
        "guards a replay in progress, and a replayed /cfg.load must not clear it"
    ),
    "diag_log.py": "the process's diagnostic log: which repeating failures it has recorded",
    "env_flags.py": "whether this process is the MCP server",
    "profile/loader.py": "a cache of a fixed schema",
    "protocol/crc.py": "a cache of crcglot's registry",
    "variables.py": (
        "launch and context variables are the process's, and SESSION_* is the running "
        "script's, which outlasts a /cfg.load inside it"
    ),
}
_CONTAINER_MUTATORS = frozenset(
    {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "setdefault",
        "update",
    }
)


def _module_state_writes(tree: ast.Module) -> set[str]:
    """Module-level names some function writes: a ``global``, an item, or a container mutator."""
    top_level: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            top_level.update(t.id for t in statement.targets if isinstance(t, ast.Name))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            top_level.add(statement.target.id)
    written: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        local = {
            node.id
            for node in ast.walk(function)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        } | {node.arg for node in ast.walk(function) if isinstance(node, ast.arg)}
        for node in ast.walk(function):
            base = None
            if isinstance(node, ast.Global):
                written.update(node.names)
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store | ast.Del):
                base = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value if node.func.attr in _CONTAINER_MUTATORS else None
            if isinstance(base, ast.Name) and base.id in top_level and base.id not in local:
                written.add(base.id)
    return written


def _host_modules():
    """``_source_modules`` less the device code, which runs on a board, not in a session."""
    for rel, tree in _source_modules():
        if "firmware/" not in rel:
            yield rel, tree


@pytest.mark.rule("config-load-deterministic")
def test_module_state_is_in_ns_or_says_why():
    """State a function keeps goes in ctx.ns(), or the allowlist says why not.

    The fresh-start comparison in ``test_config_load_state`` only sees state its
    scenario happens to touch; this catches the next global when it is written.
    The fix for a red result is ``ctx.ns()``, which the reset empties.  An
    ``on_config_load`` is not enough: that is a second way to reset, written once
    per plugin, and one forgotten line of it is the bug this guard exists for.
    """
    # Arrange / Act
    violations = [
        f"{rel}: {sorted(written)}"
        for rel, tree in _host_modules()
        if (written := _module_state_writes(tree)) and rel not in MODULE_STATE_OUTSIDE_NS
    ]

    # Assert
    assert violations == [], "module state that belongs in ctx.ns(): " + ", ".join(violations)


@pytest.mark.rule("config-load-deterministic")
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("_x = 0\ndef f():\n    global _x\n    _x = 1", {"_x"}),
        ("_x = {}\ndef f():\n    _x[1] = 2", {"_x"}),
        ("_x = []\ndef f():\n    _x.append(1)", {"_x"}),
        ("_x = {}\ndef f():\n    del _x[1]", {"_x"}),
        ("_x = {}\ndef f():\n    return _x[1]", set()),
        ("_x = {}\ndef f(_x):\n    _x[1] = 2", set()),
        ("_x = {}\ndef f():\n    _x = {}\n    _x[1] = 2", set()),
        ("_x = {}\n_x[1] = 2", set()),
    ],
)
def test_module_state_finder_sees_what_a_function_writes(body, expected):
    """The walk finds each kind of write, and not a read, a local or an import-time table."""
    # Act
    actual = _module_state_writes(ast.parse(body))

    # Assert
    assert actual == expected


@pytest.mark.rule("config-load-deterministic")
def test_module_state_allowlist_has_no_stale_entries():
    """An entry names a module that still writes module-level state."""
    # Arrange
    needing = {rel for rel, tree in _host_modules() if _module_state_writes(tree)}

    # Act
    stale = sorted(set(MODULE_STATE_OUTSIDE_NS) - needing)

    # Assert
    assert stale == [], f"take these off MODULE_STATE_OUTSIDE_NS: {stale}"


# -- broad-catch-logged ---------------------------------------------------------

# The names whose ``except`` catches everything a bug can raise.
_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException", "BoundaryException"})

# The calls that record the exception being handled: diag_log's two, and
# the TUI's event-handler net, which calls diag_log.exception itself.
_CATCH_RECORDERS = frozenset({"exception", "warning", "_report_exception"})

# Broad catches that record nothing, with why.  Only shrinks.
ALLOWED_SILENT_BROAD_CATCH: dict[str, str] = {}


def _is_broad(handler: ast.ExceptHandler) -> bool:
    """Whether ``handler`` is bare or names a broad exception, alone or in a tuple."""
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(name, ast.Name) and name.id in _BROAD_EXCEPTIONS for name in names)


def _records_or_raises(handler: ast.ExceptHandler) -> bool:
    """Whether ``handler``'s body re-raises or calls one of the :data:`_CATCH_RECORDERS`."""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "_report_exception":
                return True
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "diag_log"
                and func.attr in _CATCH_RECORDERS
            ):
                return True
    return False


def _silent_broad_catches(tree: ast.AST) -> list[int]:
    """The line of every broad ``except`` in ``tree`` that neither records nor re-raises."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and _is_broad(node) and not _records_or_raises(node)
    ]


def _termapy_code_files() -> list[Path]:
    """The package's own Python: no vendored tree, no device code (which runs on the board)."""
    return [
        path for path in _package_python_files() if "firmware" not in path.relative_to(SRC).parts
    ]


@pytest.mark.rule("broad-catch-logged")
def test_every_broad_catch_records_its_exception():
    """A broad ``except`` records the exception in the diagnostic log, or re-raises.

    What the user sees of a failure is one line; the traceback goes to
    ``diag_log`` (``exception`` or ``warning``), so a catch that stops
    anything a bug can raise is never the last anyone hears of it.  Found
    2026-11-05: about forty broad catches, and dispatch reported a
    handler's ``KeyError('port')`` as ``Plugin error (x): 'port'`` with
    the traceback kept nowhere.
    """
    # Arrange / Act
    violations = [
        f"{rel}:{lineno}"
        for path in _termapy_code_files()
        if (rel := path.relative_to(SRC).as_posix()) not in ALLOWED_SILENT_BROAD_CATCH
        for lineno in _silent_broad_catches(ast.parse(path.read_text(encoding="utf-8")))
    ]

    # Assert
    assert violations == [], (
        "a broad except records nothing:\n  "
        + "\n  ".join(violations)
        + "\n\nCall diag_log.exception(...) in the handler (the traceback is kept, the"
        "\nuser's line says where), narrow the except to what the code raises, or"
        "\nadd the FILE to ALLOWED_SILENT_BROAD_CATCH with the reason."
    )


@pytest.mark.rule("broad-catch-logged")
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("try:\n    f()\nexcept Exception:\n    pass", [3]),
        ("try:\n    f()\nexcept:\n    pass", [3]),
        ("try:\n    f()\nexcept (OSError, BoundaryException):\n    pass", [3]),
        ("try:\n    f()\nexcept BaseException as e:\n    x = e", [3]),
        ("try:\n    f()\nexcept Exception:\n    diag_log.exception('m')", []),
        ("try:\n    f()\nexcept Exception:\n    diag_log.warning('m')", []),
        ("try:\n    f()\nexcept Exception as e:\n    self._report_exception(e)", []),
        ("try:\n    f()\nexcept Exception:\n    raise", []),
        ("try:\n    f()\nexcept OSError:\n    pass", []),
        ("try:\n    f()\nexcept Exception:\n    log.exception('m')", [3]),
    ],
)
def test_broad_catch_finder_sees_each_shape(body, expected):
    """bare, named and tuple catches are broad; a diag_log call or a raise clears one."""
    # Act
    actual = _silent_broad_catches(ast.parse(body))

    # Assert
    assert actual == expected


@pytest.mark.rule("broad-catch-logged")
def test_silent_broad_catch_allowlist_has_no_stale_entries():
    """An allowlisted file still has a broad catch that records nothing."""
    # Arrange
    needing = {
        path.relative_to(SRC).as_posix()
        for path in _termapy_code_files()
        if _silent_broad_catches(ast.parse(path.read_text(encoding="utf-8")))
    }

    # Act
    stale = sorted(set(ALLOWED_SILENT_BROAD_CATCH) - needing)

    # Assert
    assert stale == [], f"take these off ALLOWED_SILENT_BROAD_CATCH: {stale}"


# ── Only the outermost operation clears the script-stop event ────────────────
#
# The shared stop event belongs to the script: a plugin that ``clear()``s it
# erases a stop the user just requested.  The engine clears it as an outermost
# script starts, and three operations at the prompt may, each the outermost
# cancellable operation when it runs and each clearing only outside a script;
# every other clear is a bug.

STOP_EVENT_NAMES = ("script_stop_event", "_script_stop")

STOP_CLEAR_ALLOWED: dict[tuple[str, str], str] = {
    (
        "repl.py",
        "ReplEngine.start_script",
    ): "the engine owns the event and clears it as an outermost script starts",
    (
        "app.py",
        "SerialTerminal._paste_on_thread",
    ): "a paste at the prompt is the outermost cancellable operation; cleared only outside a script",
    (
        "plugins/waiting.py",
        "wait",
    ): "the shared wait behind /delay, /repeat, /proto.run and /port.connect timeout=; cleared only outside a script",
    (
        "builtins/commands/run.py",
        "_replay",
    ): "/run.last at the prompt; the gate keeps it out of scripts and the handler out of a replay",
}


def _stop_clear_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every ``clear()`` on the script-stop event, direct or through a local name bound from it."""
    bound: dict[str, set[str]] = {}
    for qualname, node in _scoped_nodes(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if isinstance(node.value, ast.Attribute) and node.value.attr in STOP_EVENT_NAMES:
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.setdefault(qualname, set()).add(target.id)
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "clear"
        ):
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Attribute) and receiver.attr in STOP_EVENT_NAMES:
            sites.append((qualname, node.lineno))
        elif isinstance(receiver, ast.Name) and receiver.id in bound.get(qualname, set()):
            sites.append((qualname, node.lineno))
    return sites


@pytest.mark.rule("plugin-never-clears-stop")
def test_only_the_outermost_operation_clears_the_stop_event():
    """Nothing clears the shared script-stop event but the engine starting a script and the named operations at the prompt."""
    # Arrange / Act
    violations, _ = _unallowed(_stop_clear_sites, STOP_CLEAR_ALLOWED)

    # Assert
    assert violations == [], (
        "a plugin never clears the script-stop event; it belongs to the script:\n  "
        + "\n  ".join(violations)
        + "\n\nWait on it (is_set) and let the operation that owns it clear it, or, for"
        "\nan outermost operation at the prompt, allowlist the site with its reason."
    )


@pytest.mark.rule("plugin-never-clears-stop")
def test_stop_clear_guard_fires_on_a_probe():
    """The finder sees a direct clear, one through a local name bound from the event and the engine's own name, and passes another event's clear."""
    # Arrange
    probe = (
        "def _handler(ctx, args):\n"
        "    ctx.internal.script_stop_event.clear()\n"
        "    stop = ctx.internal.script_stop_event\n"
        "    if stop is not None:\n"
        "        stop.clear()\n"
        "    other = threading.Event()\n"
        "    other.clear()\n"
        "class Engine:\n"
        "    def start(self):\n"
        "        self._script_stop.clear()\n"
    )

    # Act
    found = _stop_clear_sites(ast.parse(probe))

    # Assert
    assert found == [("_handler", 2), ("_handler", 5), ("Engine.start", 10)], (
        "the direct, the aliased and the engine's own clear are found; the other event's is not"
    )


@pytest.mark.rule("plugin-never-clears-stop")
def test_stop_clear_allowlist_has_no_stale_entries():
    """Every allowlisted site still clears the event, so the list cannot rot."""
    # Arrange / Act
    _, used = _unallowed(_stop_clear_sites, STOP_CLEAR_ALLOWED)

    # Assert
    stale = sorted(set(STOP_CLEAR_ALLOWED) - used)
    assert stale == [], f"take these off STOP_CLEAR_ALLOWED: {stale}"


# ── Ports open in one place and are listed by one function ───────────────────
#
# TERMAPY_ALLOWED_PORTS is applied where a port opens (config.open_serial) and
# where the system's ports are listed (port_control.system_ports).  A second
# opener or a direct comports() would be a route around it (decision
# 2026-11-11).  The vendored pyserial is not scanned; termapy's own code is.

PORT_OPEN_NAMES = frozenset({"serial_for_url", "Serial"})

PORT_OPEN_ALLOWED: dict[tuple[str, str], str] = {
    ("config.py", "open_serial"): "the one opener; the port policy is applied in it first",
    (
        "port_control.py",
        "_check_in_use",
    ): "the Windows in-use probe, run only on ports system_ports allowed",
}

PORT_LIST_ALLOWED: dict[tuple[str, str], str] = {
    ("port_control.py", "system_ports"): "the one call to comports(); it applies the port policy",
}


def _port_open_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every call that builds a pyserial port: ``serial_for_url(...)`` or ``Serial(...)``."""
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name in PORT_OPEN_NAMES:
            sites.append((qualname, node.lineno))
    return sites


def _port_list_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every import of pyserial's port enumeration, ``serial.tools.list_ports``."""
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module.startswith("serial.tools.list_ports") or (
                module == "serial.tools" and any(alias.name == "list_ports" for alias in node.names)
            ):
                sites.append((qualname, node.lineno))
        elif isinstance(node, ast.Import):
            if any(alias.name.startswith("serial.tools.list_ports") for alias in node.names):
                sites.append((qualname, node.lineno))
    return sites


@pytest.mark.rule("port-policy-at-opener")
def test_only_open_serial_opens_a_port():
    """No module builds a pyserial port but config.open_serial and the in-use probe, so every open meets the port policy."""
    # Arrange / Act
    violations, _ = _unallowed(_port_open_sites, PORT_OPEN_ALLOWED)

    # Assert
    assert violations == [], (
        "open a port through config.open_serial, which applies TERMAPY_ALLOWED_PORTS:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("port-policy-at-opener")
def test_port_open_guard_fires_on_a_probe():
    """The finder sees serial_for_url and Serial, bare or through the module, and passes a simulator."""
    # Arrange
    probe = (
        "def a():\n"
        "    serial.serial_for_url('COM3')\n"
        "def b():\n"
        "    Serial('COM3')\n"
        "def c():\n"
        "    FakeSerial(baudrate=9600)\n"
    )

    # Act
    found = _port_open_sites(ast.parse(probe))

    # Assert
    assert found == [("a", 2), ("b", 4)], "both builders are found; the simulator is not"


@pytest.mark.rule("port-policy-at-opener")
def test_port_open_allowlist_has_no_stale_entries():
    """Every allowlisted opener still opens a port, so the list cannot rot."""
    # Arrange / Act
    _, used = _unallowed(_port_open_sites, PORT_OPEN_ALLOWED)

    # Assert
    stale = sorted(set(PORT_OPEN_ALLOWED) - used)
    assert stale == [], f"take these off PORT_OPEN_ALLOWED: {stale}"


@pytest.mark.rule("ports-enumerated-once")
def test_only_port_control_enumerates_ports():
    """Nothing imports pyserial's list_ports but port_control.system_ports, so every listing is filtered."""
    # Arrange / Act
    violations, _ = _unallowed(_port_list_sites, PORT_LIST_ALLOWED)

    # Assert
    assert violations == [], (
        "list ports through port_control.system_ports, which applies TERMAPY_ALLOWED_PORTS:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.rule("ports-enumerated-once")
def test_port_enumeration_guard_fires_on_a_probe():
    """The finder sees every spelling of the list_ports import and passes an unrelated serial.tools module."""
    # Arrange
    probe = (
        "def a():\n"
        "    from serial.tools.list_ports import comports\n"
        "def b():\n"
        "    from serial.tools import list_ports\n"
        "def c():\n"
        "    import serial.tools.list_ports\n"
        "def d():\n"
        "    from serial.tools.miniterm import Miniterm\n"
    )

    # Act
    found = _port_list_sites(ast.parse(probe))

    # Assert
    assert found == [("a", 2), ("b", 4), ("c", 6)], (
        "each import of the enumeration is found; miniterm is not"
    )


@pytest.mark.rule("ports-enumerated-once")
def test_port_list_allowlist_has_no_stale_entries():
    """The allowlisted enumerator still imports list_ports, so the list cannot rot."""
    # Arrange / Act
    _, used = _unallowed(_port_list_sites, PORT_LIST_ALLOWED)

    # Assert
    stale = sorted(set(PORT_LIST_ALLOWED) - used)
    assert stale == [], f"take these off PORT_LIST_ALLOWED: {stale}"
