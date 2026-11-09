# Register vendored pyserial before any test imports serial.
# This mirrors what termapy/__init__.py does at app startup.
import termapy.vendor  # noqa: F401


def _fix_msys2_path_on_windows() -> None:
    """Ensure msys2's mingw64\\bin precedes Git's mingw64\\bin in PATH.

    Symptom: under Git Bash on Windows, the CRC codegen-exec tests
    silently fail -- gcc returns rc=1 with empty stderr.  The fast
    suite, lint, and ty all stay green; only the subprocess-spawning
    codegen-exec tests fall over.

    Root cause: Git Bash prepends ``C:\\Program Files\\Git\\mingw64\\bin``
    to PATH.  When pytest spawns gcc via Python subprocess, gcc finds
    its sub-tool (cc1.exe) but Windows DLL resolution loads Git's
    older libstdc++-6 / libgcc_s_seh-1 DLLs first -- which are
    incompatible with msys2's gcc 15.x.  cc1.exe fails to load with
    NT status 0xC0000139 (STATUS_ENTRYPOINT_NOT_FOUND); gcc reports
    rc=1 with no diagnostic.

    Fix: prepend C:\\msys64\\mingw64\\bin to PATH for the test
    session, AND warn so the user knows it happened (silent fixes
    hide reality -- the user might wonder why their other tooling
    sees one gcc and pytest sees another).  No-op on Linux/macOS
    (msys2 path doesn't exist) and no-op on Windows shells where
    msys2 is already first (PowerShell, cmd with normal config).
    """
    import os
    import sys
    import warnings

    if sys.platform != "win32":
        return
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(part) for part in parts]
    norm_msys2 = os.path.normcase(msys2_bin)
    # Already first?  No-op.
    if norm and norm[0] == norm_msys2:
        return
    # Detect the specific bad condition: Git's mingw64\bin appears
    # in PATH ahead of msys2's.  Worth a loud warning in that case
    # because (a) the user's interactive gcc and pytest's gcc would
    # resolve to different installs, and (b) a permanent fix is a
    # one-line .bashrc edit.
    git_norm = os.path.normcase(r"C:\Program Files\Git\mingw64\bin")
    git_idx = norm.index(git_norm) if git_norm in norm else -1
    msys2_idx = norm.index(norm_msys2) if norm_msys2 in norm else -1
    if git_idx >= 0 and (msys2_idx < 0 or git_idx < msys2_idx):
        warnings.warn(
            f"termapy tests: prepending {msys2_bin!r} to PATH for the test "
            f"session.  Git's mingw64\\bin appears in PATH at position "
            f"{git_idx} -- ahead of msys2's at position "
            f"{'absent' if msys2_idx < 0 else msys2_idx}.  Without this fix, "
            f"pytest's gcc subprocess loads Git's libstdc++-6 DLL and "
            f"cc1.exe crashes with NT status 0xC0000139.  Make permanent "
            f"by adding 'export PATH=\"/c/msys64/mingw64/bin:$PATH\"' to "
            f"your .bashrc / .zshrc.",
            RuntimeWarning,
            stacklevel=2,
        )
    # Prepend (drop any existing later occurrence so PATH doesn't grow
    # every test session if conftest reloads).
    parts = [part for part, n in zip(parts, norm) if n != norm_msys2]
    os.environ["PATH"] = os.pathsep.join([msys2_bin] + parts)


_fix_msys2_path_on_windows()


from typing import Any  # noqa: E402 -- after the PATH fix, like every import below it

import pytest  # noqa: E402


def _restore_store(store: dict[str, Any], saved: dict[str, Any]) -> None:
    """Put one store back to its saved contents in place; the module holds the dict, not a name to rebind."""
    store.clear()
    store.update(saved)


@pytest.fixture(autouse=True, scope="session")
def _isolate_app_state(tmp_path_factory):
    """Point the app state folder at a temporary one for the whole run.

    Every session a test starts leaves a breadcrumb there (``breadcrumbs``)
    and the update check keeps its timestamps there; a test run must never
    write into the user's own app folder.  A test that needs its own folder
    still sets ``TERMAPY_STATE_DIR`` with monkeypatch, which restores this.
    """
    import os

    saved = os.environ.get("TERMAPY_STATE_DIR")
    os.environ["TERMAPY_STATE_DIR"] = str(tmp_path_factory.mktemp("app_state"))
    yield
    if saved is None:
        os.environ.pop("TERMAPY_STATE_DIR", None)
    else:
        os.environ["TERMAPY_STATE_DIR"] = saved


@pytest.fixture(autouse=True, scope="session")
def _isolate_diag_log(tmp_path_factory):
    """Point the diagnostic log at a temporary folder for the whole run.

    Every session a test starts opens one (``diag_log.start``), and a
    subprocess run in the repository would otherwise find the developer's
    own ``termapy_cfg/`` and write its log there.  Subprocesses inherit the
    variable.
    """
    import os

    from termapy.diag_log import DIAG_DIR_ENV

    saved = os.environ.get(DIAG_DIR_ENV)
    os.environ[DIAG_DIR_ENV] = str(tmp_path_factory.mktemp("diag_log"))
    yield
    if saved is None:
        os.environ.pop(DIAG_DIR_ENV, None)
    else:
        os.environ[DIAG_DIR_ENV] = saved


def _restore_nested(store: dict[str, Any], saved: dict[str, Any]) -> None:
    """Put a nested dict back to ``saved`` in place, keeping each inner dict's identity.

    A module may hold an inner dict of the defaults by reference
    (``DEFAULT_CFG["serial"]``), so a section is refilled, never replaced.
    """
    for key in [key for key in store if key not in saved]:
        del store[key]
    for key, value in saved.items():
        current = store.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _restore_nested(current, value)
        else:
            store[key] = value


@pytest.fixture(autouse=True)
def _defaults_untouched():
    """Fail a test that leaves ``DEFAULT_CFG`` changed, then put it back (registry ``defaults-untouched``).

    ``DEFAULT_CFG`` is the process's one copy of the defaults.  A rig that
    builds its config as ``{**DEFAULT_CFG, ...}`` shares the nested sections
    with it, so a setting a test changed (``json_fmt.indent``) became the
    default for every later test in that worker, and tests failed at random
    far from the cause.  Failing here names the test; restoring keeps the
    rest of the worker on the real defaults.  A rig copies them with
    ``copy.deepcopy``.
    """
    import copy

    from termapy.defaults import DEFAULT_CFG

    before = copy.deepcopy(DEFAULT_CFG)
    yield
    if DEFAULT_CFG == before:
        return
    changed = sorted(
        key for key in before.keys() | DEFAULT_CFG.keys() if before.get(key) != DEFAULT_CFG.get(key)
    )
    _restore_nested(DEFAULT_CFG, before)
    pytest.fail(
        f"this test changed DEFAULT_CFG ({', '.join(changed)}); "
        "build a rig's config from copy.deepcopy(DEFAULT_CFG)",
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _isolate_variables():
    """Hand every test the ``$(NAME)`` stores as they were before it ran (registry ``variables-isolated``).

    ``termapy.variables`` keeps launch, context and datetime variables in three
    module-level stores, and a frontend registers ``$(CFG)`` and the launch vars
    into them when it is built.  Shared by every test in a worker, they carried
    one test's state into the next: the echo test passed only after some earlier
    test had built a CLI on a config named ``rig.cfg``.  Restoring rather than
    clearing keeps whatever import or a wider-scoped fixture put there.
    """
    from termapy import variables

    stores = (
        variables._LAUNCH_VARS,
        variables._CONTEXT_VARS,
        variables._FROZEN_MOMENTS,
    )
    saved = [dict(store) for store in stores]
    yield
    for store, before in zip(stores, saved):
        _restore_store(store, before)


@pytest.fixture
def cli(tmp_path):
    """The DEMO rig: a ``CLITerminal`` connected to the DEMO port, its config at ``tmp_path/rig/rig.cfg``.

    Commands are driven the way a user or a script drives them,
    ``cli.repl.dispatch_full("/cmd")``, against the simulated device.
    Seven test files carried this fixture word for word until 2026-11-06.  A
    file that needs another rig (a plugin in the config folder, another port,
    an unconnected CLI) defines its own ``cli``, which pytest uses in place of
    this one for that file.
    """
    import copy
    import json

    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {
        **copy.deepcopy(DEFAULT_CFG),
        "serial": {**default_serial, "port": "DEMO", "baud_rate": 115200},
    }
    config_path = tmp_path / "rig" / "rig.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    # The checkout has a real termapy_cfg/plugin/; keep it out of the lifecycle.
    terminal.repl.global_root = tmp_path
    assert terminal._connect() is True, "the DEMO port opens"
    yield terminal
    terminal._disconnect()


def pytest_addoption(parser) -> None:
    """Add ``--run-hw-loopback`` (see ``tests/test_hw_loopback.py``) and ``--rule``."""
    parser.addoption(
        "--rule",
        default=None,
        metavar="ID",
        help=(
            "Run only the tests marked @pytest.mark.rule(ID): a docs/rules.md row or a "
            "spec section id (tests/traceability.py SOURCES; docs/traceability.md)."
        ),
    )
    parser.addoption(
        "--run-hw-loopback",
        action="store_true",
        default=False,
        help=(
            "Run the hw_loopback tests (hardware loopback).  Needs a TX-RX-jumpered "
            "USB-serial adapter (found by serial number, TERMAPY_HW_LOOPBACK_SN). "
            "Off by default because their measurements are only valid when "
            "they are not competing for CPU -- run with -n 0."
        ),
    )
    parser.addoption(
        "--require-hw-loopback",
        action="store_true",
        default=False,
        help=(
            "Like --run-hw-loopback, but a jumpered adapter that is absent, busy, "
            "ambiguous or not echoing FAILS the run instead of skipping it.  "
            "release_prep passes it, so a release cannot read green with the "
            "bench unplugged."
        ),
    )


def pytest_configure(config) -> None:
    """Refuse both hardware flags on GitHub Actions, before anything is collected.

    The hw_loopback suite must never run there, not even far enough to
    enumerate a runner's serial ports: a refusal at configure time stops the
    session before its fixture exists, and it is loud, so a workflow
    edit that adds a hardware flag turns CI red with this message rather than
    quietly skipping.  GitHub sets ``GITHUB_ACTIONS`` on every hosted and
    self-hosted runner; its presence is the signal, a switch rather than a
    boolean read from user text (the ``bool-one-vocabulary`` exemption).
    ``sw_loopback`` tests (pyserial's ``loop://``) are untouched: they need
    no hardware and run on GitHub like every other test.
    """
    import os

    import pytest

    if "GITHUB_ACTIONS" not in os.environ:
        return
    demanded = [
        flag
        for flag, dest in (
            ("--run-hw-loopback", "run_hw_loopback"),
            ("--require-hw-loopback", "require_hw_loopback"),
        )
        if config.getoption(dest)
    ]
    if demanded:
        raise pytest.UsageError(
            f"{' '.join(demanded)} refused: the hw_loopback suite never runs on "
            f"GitHub Actions (registry hw-loopback-never-on-github); run it on the bench"
        )


def _check_rule_markers(items) -> None:
    """Refuse the session when a ``rule`` marker names nothing (registry ``rules-registry``).

    The id must be one an id source holds (``tests/traceability.py``), so a
    typo or a retired rule fails the run before a test runs instead of
    tagging tests with a name nobody owns.  Imported here, not at the top:
    ``test_defaults_untouched.py`` loads this file by path from a scratch
    folder, where ``tests/`` is not on the path, to bind one fixture.
    """
    import pytest
    from traceability import MARKER, known_ids, marker_problems

    problems = marker_problems(
        (
            (item.nodeid, tuple(marker.args))
            for item in items
            for marker in item.iter_markers(MARKER)
        ),
        known_ids(),
    )
    if problems:
        raise pytest.UsageError(
            "rule markers that name nothing (docs/traceability.md):\n  " + "\n  ".join(problems)
        )


def _select_rule(config, items) -> None:
    """Under ``--rule ID``, keep only the tests whose ``rule`` markers name ``ID``."""
    import pytest
    from traceability import MARKER, known_ids

    wanted = config.getoption("--rule")
    if wanted is None:
        return
    if wanted not in known_ids():
        raise pytest.UsageError(f"--rule {wanted}: no id source holds it (docs/traceability.md)")
    selected = [
        item
        for item in items
        if any(marker.args[:1] == (wanted,) for marker in item.iter_markers(MARKER))
    ]
    deselected = [item for item in items if item not in selected]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def pytest_collection_modifyitems(config, items) -> None:
    """Deselect hardware tests unless ``--run-hw-loopback`` was passed; check and select by rule.

    An opt-in FLAG rather than a marker filter, because pytest's ``-m`` is a
    single expression: a ``-m 'not hardware'`` default in ``addopts`` is
    silently replaced by any ``-m`` on the command line, so ``-m slow`` would
    quietly pull the hardware tests back in.  A flag composes with every
    ``-m`` the project already documents.

    Why they are off by default at all: they measure DATA LOSS at 921600 with
    a deliberately slow consumer.  Run alongside a full ``-n auto`` suite, CPU
    contention starves the reader hard enough to lose ~18% -- indistinguishable
    from the ~22% regression signature they exist to catch.  The loss there is
    host-load-induced at the USB layer, not a termapy defect, so including them
    in the parallel suite would produce a confidently wrong red.
    """
    import pytest

    _check_rule_markers(items)
    _select_rule(config, items)
    if config.getoption("--run-hw-loopback") or config.getoption("--require-hw-loopback"):
        return
    skip = pytest.mark.skip(
        reason="hw_loopback: needs --run-hw-loopback and the jumpered adapter; "
        "run: uv run pytest --run-hw-loopback -m hw_loopback -n 0"
    )
    for item in items:
        if "hw_loopback" in item.keywords:
            item.add_marker(skip)
