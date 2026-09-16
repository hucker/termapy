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
    ("termapy.builtins.commands._cfg_icon", "retire_launcher"),
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
# user input against these is how ``/term.request yes`` gets rejected and
# ``echo=false`` silently means off; the sanctioned readers are
# ``ParamSpec(type="bool")``, ``parse_bool_setting`` and bare ``parse_bool``.
_BOOL_TOKENS: frozenset[str] = frozenset({"on", "off", "true", "false", "yes", "no"})

# Command-surface modules scanned in addition to builtins/.
_BOOL_SCAN_EXTRA: tuple[str, ...] = ("repl.py", "cli.py", "app.py", "app_hooks.py")


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
                        element for element in operand.elts
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


def test_booleans_go_through_parse_bool():
    """No command code compares tokens to on/off or declares on/off enums.

    One vocabulary, three sanctioned readers (see CLAUDE.md): the ``bool``
    param type, ``parse_bool_setting`` for settings, bare ``parse_bool``
    in documented hand-rolled parsers.  A literal comparison accepts only
    the tokens the author remembered (``/term.request yes`` was rejected)
    and usually treats typos as False instead of erroring.
    """
    # Arrange
    targets = sorted(BUILTINS_DIR.rglob("*.py")) + [
        SRC / name for name in _BOOL_SCAN_EXTRA
    ]
    violations: list[str] = []

    # Act
    for path in targets:
        rel = str(path.relative_to(SRC.parent)).replace("\\", "/")
        violations.extend(
            _bool_token_violations(ast.parse(path.read_text(encoding="utf-8")), rel)
        )

    # Assert
    assert violations == [], (
        "boolean tokens must go through parse_bool / ParamSpec(type='bool') / "
        "parse_bool_setting, never literal comparisons:\n  "
        + "\n  ".join(violations)
    )


def test_boolean_guard_fires_on_a_probe():
    """The guard actually detects the patterns it exists to forbid."""
    # Arrange -- the three shapes the audit found in the wild
    probe = (
        "x = tok == 'on'\n"
        "y = tok in ('on', 'off', 'toggle')\n"
        "z = EnumValue('on')\n"
    )

    # Act
    found = _bool_token_violations(ast.parse(probe), "probe")

    # Assert
    assert len(found) == 3, "a comparison, a membership test, and an enum all flagged"


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
ALLOWED_RAW_MKDIR: frozenset[str] = frozenset({
    "termapy/app_dirs.py",
    "termapy/builtins/commands/_cfg_icon.py",
    "termapy/builtins/commands/app.py",
    "termapy/config.py",
    "termapy/devices/__init__.py",
    "termapy/dialogs/config_editor.py",
    "termapy/folders.py",
    "termapy/mcp/server.py",
})


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
    return sorted(
        path for path in SRC.rglob("*.py")
        if "vendor" not in path.relative_to(SRC).parts
    )


def _raw_mkdir_files() -> dict[str, list[str]]:
    """``{rel: [violation lines]}`` for every package file with a raw mkdir."""
    found: dict[str, list[str]] = {}
    for path in _package_python_files():
        rel = path.relative_to(SRC.parent).as_posix()
        calls = _raw_mkdir_calls(ast.parse(path.read_text(encoding="utf-8")), rel)
        if calls:
            found[rel] = calls
    return found


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
