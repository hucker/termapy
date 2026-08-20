"""Structural rules checked against the source tree itself.

These read the SOURCE rather than exercising behavior, because the rules they
enforce are invisible at runtime: an import that crosses a layer boundary works
fine until someone has to unpick it months later.  Keeping them here rather
than in a CI-only grep means they run in ``uv run pytest``, which is the loop
that actually happens before a commit -- a guard you only meet after pushing is
a guard you meet too late.
"""

from __future__ import annotations

import ast
from pathlib import Path

import termapy

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
ALLOWED_CORE_IMPORTS: frozenset[tuple[str, str]] = frozenset({
    # (1) Published plugin functions.
    ("termapy.builtins.commands.find", "dismiss"),
    ("termapy.builtins.commands.edit", "make_edit_handler"),
    ("termapy.builtins.commands.edit", "make_explore_handler"),
    ("termapy.builtins.commands.edit", "make_list_handler"),
    # (2) Helper modules under commands/ that are not plugins.
    ("termapy.builtins.commands._cfg_icon", "find_launcher_for_cfg"),
    ("termapy.builtins.commands._cfg_icon", "remove_launcher_at"),
    ("termapy.builtins.commands", "_run_record"),
})


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


# ── Engine-internal IO primitives ────────────────────────────────────────────

# ``ctx.io._write`` / ``._write_markup`` are the raw primitives the frontends
# implement.  Handlers use the semantic channels (result / output / status)
# instead, so the frontend decides how each kind of message is rendered.
IO_PRIMITIVES: frozenset[str] = frozenset({"_write", "_write_markup"})


def _plugin_python_files() -> list[Path]:
    """Every .py file under builtins/ -- the plugins themselves."""
    return sorted(BUILTINS_DIR.rglob("*.py"))


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


# ── UI-layer confinement ─────────────────────────────────────────────────────

# Textual may only be imported by the UI layer.  Everything else -- the REPL
# engine, the plugin system, the serial stack, and every built-in command --
# stays importable in CLI and MCP hosts, which never load Textual at all.
UI_LAYER_PACKAGES: frozenset[str] = frozenset({"dialogs", "widgets"})
UI_LAYER_MODULES: frozenset[str] = frozenset({
    "app.py",
    "capture_view.py",
    "info_views.py",
    "palette_provider.py",
    "proto_debug.py",
    "title_bar.py",
})


def _imports_package(tree: ast.AST, root: str) -> bool:
    """True when the module imports ``root`` or anything beneath it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == root or alias.name.startswith(f"{root}.")
                for alias in node.names
            ):
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
    assert missing == [], (
        "UI_LAYER_MODULES names modules that no longer exist: " + ", ".join(missing)
    )


def test_engine_modules_have_no_serial_dependency():
    """The plugin system and the scripting helpers stay transport-agnostic.

    ``plugins/`` defines the command/context API and ``scripting.py`` is pure
    functions; neither should know that a serial port exists.  Keeping pyserial
    out of them is what lets a non-serial frontend reuse the whole command
    layer.
    """
    # Arrange
    targets = [*(SRC / "plugins").rglob("*.py"), SRC / "scripting.py"]
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
