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
import re
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
    """core-imports-published-only: Core may use a plugin's published surface, never its internals.

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
    """io-semantic-channels: Handlers must reach the terminal through the semantic channels.

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
    """ui-layer-textual: Only the UI layer may import Textual.

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
    """engine-transport-agnostic: plugins/, scripting.py, symbols/ and memory.py stay transport-agnostic.

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
        path for path in SRC.rglob("*.py")
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
                        element for element in operand.elts
                        if isinstance(element, ast.Constant)
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
                        element for element in operand.args[0].elts
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
    """bool-one-vocabulary: every text-to-boolean read under src/termapy goes through parse_bool; no module outside the allowlist compares a token to the vocabulary.

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
        "parse_bool_setting, never literal comparisons:\n  "
        + "\n  ".join(violations)
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
    assert not any(rel.startswith("termapy/vendor/") for rel in scanned), "the vendored tree is never scanned"


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
    """folders-ensure-only: No code outside the allowlist creates a folder with a raw ``mkdir``.

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


def test_symbols_namespace_is_touched_only_by_its_session_module():
    """symbols-one-owner: No code outside ``symbols/session.py`` calls ``ctx.ns("symbols")``."""
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


# ── Test headers ─────────────────────────────────────────────────────────────

TESTS = Path(__file__).parent

# Test files that still hold a test with no docstring header, written before
# the rule existed.  The list only shrinks: files come off as batches convert
# them (the stale-entry test below forces that), and when it is empty this
# constant and its stale-entry test are deleted.  Never add a file here -- a
# new or edited test gets its header instead.
ALLOWED_HEADERLESS: frozenset[str] = frozenset({
    "test_devices.py",
    "test_devices_svd.py",
    "test_firmware_mem.py",
    "test_memory_views.py",
    "test_sym_commands.py",
})


def _test_functions(
    body: list[ast.stmt], prefix: str = "",
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


def test_every_test_has_a_header():
    """test-headers: Every test outside the shrinking allowlist says what it pins in a docstring.

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
# assumption = review only) and the test.  The guard below keeps the table and
# the guards in this file agreeing both ways, so a renamed test cannot leave a
# row pointing at nothing and a new guard cannot ship unlisted.

REGISTRY = SRC.parent.parent / "docs" / "rules.md"

_RULE_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
_TEST_REF = re.compile(r"\btest_\w+(?:\.py)?")
# A guard's companions prove it fires or keep its allowlist honest; they carry
# no id of their own, so the reverse check skips them by name.
_COMPANION_MARKERS = ("fires_on_a_probe", "has_no_stale_entries", "still_touches_it")


def _registry_rows(text: str) -> list[dict[str, str]]:
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


def _collected_tests() -> tuple[set[str], set[str]]:
    """``(function names, file names)`` of every test under tests/."""
    names: set[str] = set()
    files: set[str] = set()
    for path in TESTS.rglob("test_*.py"):
        files.add(path.name)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(qualname.rsplit(".", 1)[-1] for qualname, _ in _test_functions(tree.body))
    return names, files


def _guard_headers(tree: ast.Module) -> list[tuple[str, str]]:
    """``(name, first docstring line)`` of every guard in a module, companions excluded."""
    found: list[tuple[str, str]] = []
    for qualname, func in _test_functions(tree.body):
        name = qualname.rsplit(".", 1)[-1]
        if any(marker in name for marker in _COMPANION_MARKERS):
            continue
        doc = ast.get_docstring(func) or ""
        found.append((name, doc.splitlines()[0] if doc else ""))
    return found


def _registry_mismatches(
    rows: list[dict[str, str]],
    test_names: set[str],
    test_files: set[str],
    guards: list[tuple[str, str]],
) -> list[str]:
    """Every way the registry and the guards can disagree, as one line each."""
    problems: list[str] = []
    ids: set[str] = set()
    for row in rows:
        rid = row.get("id", "")
        if not _RULE_ID.match(rid):
            problems.append(f"registry: bad id {rid!r}")
        elif rid in ids:
            problems.append(f"registry: duplicate id {rid}")
        ids.add(rid)
        kind = row.get("kind", "")
        if kind not in ("guarantee", "assumption"):
            problems.append(f"registry {rid}: kind must be guarantee or assumption, not {kind!r}")
        refs = _TEST_REF.findall(row.get("enforced by", ""))
        if kind == "guarantee" and not refs:
            problems.append(f"registry {rid}: a guarantee names the test that enforces it")
        for ref in refs:
            known = test_files if ref.endswith(".py") else test_names
            if ref not in known:
                problems.append(f"registry {rid}: no such test {ref}")
    for name, header in guards:
        prefix, _, _ = header.partition(":")
        if prefix not in ids:
            problems.append(
                f"guard {name}: header must start with a registered id, got {header[:40]!r}"
            )
    return problems


def test_rules_registry_matches_the_guards():
    """rules-registry: docs/rules.md and the guards in this file agree both ways.

    A row's tests exist, so a guarantee cannot name a test that was renamed
    away; every guard here carries a registered id in its header, so a new
    guard cannot ship unlisted.  The registry's intro says how to add a row.
    """
    # Arrange / Act
    rows = _registry_rows(REGISTRY.read_text(encoding="utf-8"))
    names, files = _collected_tests()
    guards = _guard_headers(ast.parse(Path(__file__).read_text(encoding="utf-8")))
    problems = _registry_mismatches(rows, names, files, guards)

    # Assert
    assert problems == [], "\n  ".join(["registry and guards disagree:", *problems])


def test_registry_guard_fires_on_a_probe():
    """The guard detects a bad id, a bad kind, a missing test, a bare guarantee, and an unregistered guard."""
    # Arrange -- one good row and one of each defect, plus one listed and
    # one unlisted guard
    rows = [
        {"id": "good-id", "kind": "guarantee", "enforced by": "test_real"},
        {"id": "BadId", "kind": "guarantee", "enforced by": "test_real"},
        {"id": "no-kind", "kind": "rule", "enforced by": "test_real"},
        {"id": "gone-test", "kind": "guarantee", "enforced by": "test_renamed_away"},
        {"id": "bare-one", "kind": "guarantee", "enforced by": "review"},
        {"id": "by-file", "kind": "guarantee", "enforced by": "test_file.py"},
    ]
    guards = [
        ("test_listed", "good-id: pins it"),
        ("test_unlisted", "Pins something nobody wrote down."),
    ]

    # Act
    problems = _registry_mismatches(rows, {"test_real"}, {"test_file.py"}, guards)

    # Assert
    assert problems == [
        "registry: bad id 'BadId'",
        "registry no-kind: kind must be guarantee or assumption, not 'rule'",
        "registry gone-test: no such test test_renamed_away",
        "registry bare-one: a guarantee names the test that enforces it",
        "guard test_unlisted: header must start with a registered id, "
        "got 'Pins something nobody wrote down.'",
    ], "one line per defect, nothing for the good row or the listed guard"


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

KNOWN_UNTESTED_COMMANDS: frozenset[str] = frozenset({
    "app.list", "app.state.edit", "cap.hex", "cfg.help",
    "edit.info", "edit.log", "edit.plugin.list", "edit.proto.list", "edit.run.list",
    "help.plugin", "help.target", "ping.quiet",
    "print.r",
    "term.encoding", "term.log", "term.send_bare_enter",
    "xfer.root", "xfer.xmodem.recv", "xfer.xmodem.send", "xfer.ymodem.send",
})


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


def test_every_command_is_mentioned_by_a_test():
    """command-has-test: every registered command is named by a test, except the shrinking known-untested set.

    A family generated from one handler counts once.  A new command fails
    this until a test names it; the fix is the test, never a new entry in
    the allowlist.
    """
    # Arrange / Act
    untested = _untested_groups(_command_groups(), _tests_text())
    violations = [
        " ".join(names) for names in untested if not any(name in KNOWN_UNTESTED_COMMANDS for name in names)
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
    tests_text = 'eng.dispatch("cap.text t.txt")\nresult = run("/port.open_wide")\nuse("/proto.crc.rust")\n'

    # Act
    untested = _untested_groups(groups, tests_text)

    # Assert
    assert untested == [["port.open"]], "a prefix of a longer token is not a mention; one member covers a family"


def test_known_untested_commands_has_no_stale_entries():
    """Every allowlisted command is still untested, so the set only shrinks."""
    # Arrange / Act
    still_untested = {name for names in _untested_groups(_command_groups(), _tests_text()) for name in names}

    # Assert
    stale = sorted(KNOWN_UNTESTED_COMMANDS - still_untested)
    assert stale == [], (
        "KNOWN_UNTESTED_COMMANDS lists commands a test now names; drop them:\n  "
        + "\n  ".join(stale)
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
    module_marks = _mark_names([
        stmt.value
        for stmt in tree.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in stmt.targets)
    ])

    unlabeled: list[str] = []
    in_tests: set[int] = set()

    def visit(body: list[ast.stmt], prefix: str, inherited: set[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                uses = _loop_url_constants(node, docstrings)
                in_tests.update(uses)
                if uses and "sw_loopback" not in inherited | _mark_names(node.decorator_list):
                    unlabeled.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                visit(node.body, f"{prefix}{node.name}.", inherited | _mark_names(node.decorator_list))

    visit(tree.body, "", module_marks)
    if _loop_url_constants(tree, docstrings) - in_tests and "sw_loopback" not in module_marks:
        unlabeled.append("<module>")
    return unlabeled


def test_sw_loopback_tests_are_labeled():
    """sw-loopback-labeled: every test that uses pyserial's loop:// carries the sw_loopback marker.

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
    assert actual == ["test_unlabeled", "<module>"], "the unlabeled test, and the helper with no module mark"


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
    ("protocol/core.py", "_match_hex_byte"): "the one hex-byte tokenizer behind parse_data, parse_data_segments and parse_pattern",
    ("symbols/address.py", "parse_number"): "the published number reader",
    ("port_format.py", "split_vid_pid"): "the USB id reader; USB ids are hex by convention",
    ("cli_flags.py", "_parse_hex_int"): "a --ports VID/PID filter, hex by USB convention",
    ("demo.py", "FakeSerial._parse_hex_addr"): "the simulated device's own grammar",
    ("demo.py", "FakeSerial._handle_mem_native"): "the simulated device's own grammar",
    ("demo.py", "FakeSerial._handle_mem"): "the simulated device's own grammar",
    ("memory.py", "parse_reply"): "a MEM reply from the device, not user text",
    ("memory.py", "TemplateDialect.parse_read"): "a device's read reply, not user text",
    ("response_parsers.py", "_coerce"): "device response text",
    ("builtins/commands/proto.py", "_detect_ascii"): "a packet's ASCII-hex CRC trailer, device text",
    ("symbols/converters/xc32.py", "convert"): "a linker map's own address format",
    ("symbols/table.py", "parse_addr_field"): "a symbol file's 0x address field",
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
            base = node.args[1] if len(node.args) > 1 else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "base"), None
            )
            if isinstance(base, ast.Constant) and base.value == 16:
                sites.append((qualname, node.lineno))
    return sites


def _lowercase_hex_sites(tree: ast.Module) -> list[tuple[str, int]]:
    """``(qualname, line)`` of every lowercase hex rendering.

    A ``.hex()`` call not followed by ``.upper()``, an f-string spec ending
    in ``x``, a ``format(..., "...x")``, a ``%x`` string, and builtin ``hex()``.
    """
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    sites: list[tuple[str, int]] = []
    for qualname, node in _scoped_nodes(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "hex":
            parent = parents.get(id(node))
            if not (isinstance(parent, ast.Attribute) and parent.attr == "upper"):
                sites.append((qualname, line))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hex":
            sites.append((qualname, line))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "format":
            spec = node.args[1] if len(node.args) > 1 else None
            if isinstance(spec, ast.Constant) and isinstance(spec.value, str) and spec.value.endswith("x"):
                sites.append((qualname, line))
        elif isinstance(node, ast.FormattedValue) and isinstance(node.format_spec, ast.JoinedStr):
            spec = "".join(
                part.value for part in node.format_spec.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if spec.endswith("x"):
                sites.append((qualname, getattr(node.value, "lineno", line)))
        elif (
            isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
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


def _unallowed(finder, allowed: dict[tuple[str, str], str]) -> tuple[list[str], set[tuple[str, str]]]:
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


def test_hex_is_parsed_only_by_the_published_readers():
    """hex-one-reader: user hex is read only by parse_hex / parse_data / parse_pattern / parse_number.

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


def test_hex_output_is_uppercase():
    """hex-output-uppercase: every hex termapy prints or returns is uppercase; .hex() is always .hex().upper()."""
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
    source = '''
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
'''
    tree = ast.parse(source)

    # Act
    parsed = _hex_parse_sites(tree)
    lowercase = _lowercase_hex_sites(tree)

    # Assert
    assert [line for _, line in parsed] == [3, 4, 5, 6], "fromhex, int(x, 16) both ways, unhexlify; not int(x)"
    assert [line for _, line in lowercase] == [10, 11, 12, 13, 14, 15], (
        "bare .hex() twice, hex(), a lowercase f-spec, format(), printf; not .upper() or :02X"
    )


def test_hex_allowlist_has_no_stale_entries():
    """Every allowlisted function still reads or renders hex, so both allowlists only shrink."""
    # Arrange / Act
    _, parse_used = _unallowed(_hex_parse_sites, HEX_PARSE_ALLOWED)
    _, lower_used = _unallowed(_lowercase_hex_sites, HEX_LOWERCASE_ALLOWED)

    # Assert
    stale = sorted(set(HEX_PARSE_ALLOWED) - parse_used) + sorted(set(HEX_LOWERCASE_ALLOWED) - lower_used)
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
    return sorted({
        keyword.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Command"
        for keyword in node.keywords
        if keyword.arg == "handler"
        and isinstance(keyword.value, ast.Name)
        and not _HANDLER_NAME.match(keyword.value.id)
    })


def test_handlers_follow_one_naming_shape():
    """handler-naming: every command handler is `_handler` or `_handler_<sub>`, in built-ins, demo and example plugins."""
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
    source = '''
COMMAND = Command(name="x", handler=_handler, sub_commands={
    "a": Command(handler=_handler_a),
    "b": Command(handler=_handler_crc_calc),
    "c": Command(handler=_cmd_send),
    "d": Command(handler=_proto_root_handler),
    "e": Command(handler=_make_list_handler("x", "*")),
    "f": Command(handler=_HandlerTypo),
})
'''

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

MARKUP_SINKS = frozenset({
    "result_markup", "output_markup", "status_markup",
    "_write_markup", "write_markup", "_write_output_markup", "from_markup",
})
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
        for node in tree.body if isinstance(node, ast.Assign) and "[/" in _literal(node.value)
        for target in node.targets if isinstance(target, ast.Name)
    }
    sink_args = {
        id(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and node.args
        and (getattr(node.func, "attr", None) or getattr(node.func, "id", None)) in MARKUP_SINKS
    }
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # A placeholder stands where each value goes, so "[{step}/{total}]" is not "[/]".
            shape = "".join(_literal(part) or "\0" for part in node.values)
            if id(node) in sink_args or "[/" in shape:
                sites.extend((part.lineno, ast.unparse(part.value)) for part in _raw_interpolations(node))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            receiver = node.func.value
            if "[/" in _literal(receiver) or (isinstance(receiver, ast.Name) and receiver.id in templates):
                sites.append((node.lineno, f"{ast.unparse(receiver)}.format(...)"))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and "[/" in _literal(node.left):
            sites.append((node.lineno, "markup % ..."))
        if (
            isinstance(node, ast.Call) and node.args
            and isinstance(node.func, ast.Attribute) and node.func.attr in PLAIN_CHANNELS
            and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "io"
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


def test_text_reaches_markup_escaped():
    """markup-escape: every value interpolated into Rich markup is escape_markup()'d, `_markup`-named, a style, or a number."""
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
    source = '''
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
'''

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
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def _authored(node: ast.expr, constants: set[str]) -> bool:
    """True for a string the author wrote (a literal, or a module-level string constant) or a ``Text``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.Name) and node.id in constants:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        return (isinstance(func, ast.Name) and func.id == "Text") or (
            isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "Text"
        )
    return False


def _widget_markup_sites(tree: ast.Module) -> list[tuple[int, str]]:
    """``(line, call)`` for every widget or notification built from text that Textual would parse."""
    constants = {
        target.id
        for node in tree.body if isinstance(node, ast.Assign) and _literal(node.value)
        for target in node.targets if isinstance(target, ast.Name)
    }
    sites: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _MARKUP_KW_WIDGETS | {"Button", "notify"}:
            continue
        keyword = {"Button": "label", "notify": "message"}.get(name, "content")
        content = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == keyword), None
        )
        markup_off = any(
            kw.arg == "markup" and isinstance(kw.value, ast.Constant) and kw.value.value is False
            for kw in node.keywords
        )
        if content is None or markup_off or _authored(content, constants):
            continue
        sites.append((node.lineno, f"{name}({ast.unparse(content)})"))
    return sorted(sites)


def test_widget_text_is_never_parsed_as_markup():
    """widget-text-literal: a Static, Label, Button or notify built from text has markup=False or a Text."""
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
    source = '''
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
'''

    # Act
    actual = [line for line, _ in _widget_markup_sites(ast.parse(source))]

    # Assert
    assert actual == [5, 6, 7, 8, 9], (
        "text into Static, Label, Button, notify and a content= keyword is flagged; "
        "literals, module constants, markup=False and Text pass"
    )
