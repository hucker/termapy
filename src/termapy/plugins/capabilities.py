"""Capability model: what a command needs and what a frontend provides, checked before dispatch.

Every command declares *what the environment must provide* for its handler
to run.  That declaration is a ``CapabilitySet`` on the ``Command`` (and
carried through to the registered ``PluginInfo``).  Every execution
environment (REPL prompt, script runner, CLI, TUI) publishes the
capabilities it provides on ``PluginContext.capabilities``.

Dispatch is a simple check: if the command's ``needs`` aren't satisfied
by the context's ``capabilities``, the command fails with a clear message
naming what's missing.  No special cases; commands that can run anywhere
declare an empty ``needs`` (the default).

**This is a fundamental aspect of every command.**  Handlers may not
silently no-op when a capability is missing -- they must either declare
the need (so dispatch gates them) or not use the capability at all.

Why a closed dataclass of booleans rather than a free-form set of strings?

  - Typos fail at import time (``needs=CapabilitySet(bloc_until=True)``
    is an immediate error), not silently at runtime.
  - The fields below are the single source of truth for the vocabulary.
    Grep-friendly: every consumer reads ``caps.block_until`` by name.
  - Extending is cheap: add a field with a default of ``False``, and
    every environment and command stays source-compatible.

Add a capability by:

  1. Add a new field here declared with ``_restricted("<hint>")`` -- the
     hint is what /help prints after "requires" -- and a comment
     explaining *what* it means and *where* it's provided.  Session
     state the user can change adds ``state=`` (the gate's answer) and
     ``attainable=True``.
  2. The environments that provide it set the field to True when they
     build ``ctx.capabilities`` (typically in ``app.py`` / ``cli.py`` /
     the script runner in ``repl.py``).
  3. Commands that require it set the field in their ``Command.needs``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, fields, replace
from typing import ClassVar


class MissingCapability(Exception):
    """Raised when a handle method is invoked without the required capability.

    Handle methods on capability-gated namespaces (e.g. ``ctx.ui.confirm``,
    ``ctx.fs.open_file``) check the host's ``CapabilitySet`` and raise this
    exception if the required flag is not provided.

    The dispatcher's existing capability gate at ``ReplEngine.dispatch_full()``
    catches the common case (a command declared ``needs=CapabilitySet(...)``
    that the environment can't satisfy) before the handler runs.  This
    exception is the backstop: a command that didn't declare the right
    needs but called a gated method anyway.

    Caught at the dispatcher's boundary-exception handler and converted to
    ``CmdResult.fail``, so a misbehaving plugin can't crash the host.
    """


def _restricted(hint: str, *, state: str | None = None, attainable: bool = False) -> bool:
    """A restrictive capability: off by default, with what /help says it requires.

    Everything termapy says about a capability lives on its field; the
    module tables below (``REQUIREMENT_HINTS``, ``STATE_MESSAGES``,
    ``ATTAINABLE``) are computed from it (registry ``capability-declared-once``).

    Args:
        hint: /help's REQUIRED CAPABILITIES row: a noun phrase completing
            "requires ...", never a sentence, a "when" clause or a bare
            location, and no period.
        state: The dispatch gate's answer when this session state is all a
            command lacks (``No config loaded.``), instead of "not available
            in this environment".
        attainable: Any frontend can reach it during a session (load a
            config, open a port, run a script), so "can this command ever
            run here" (/help AVAILABLE, the MCP catalog) counts it as yes; a
            fixed property of the frontend (a desktop, the TUI itself) is not.
    """
    return field(default=False, metadata={"hint": hint, "state": state, "attainable": attainable})


@dataclass(frozen=True)
class CapabilitySet:
    """Declarative set of environment capabilities.

    Serves two roles with the same shape:

      - ``Command.needs`` -- what a handler requires to run.
      - ``PluginContext.capabilities`` -- what the environment provides.

    A command is allowed to run when::

        command.needs.satisfied_by(ctx.capabilities)

    Fields come in two groups with **different defaults**:

      - **Baseline** (default ``True``): things every execution environment
        that termapy ships is guaranteed to provide (terminal output,
        serial I/O, nested dispatch, config access).  A command declares
        these by leaving them alone; they show up in ``CapabilitySet()``
        automatically.  A hypothetical restricted environment (sand-boxed
        runner, web preview) can flip one ``False`` and dispatch will
        gate every command that depends on it, without any command
        author needing to change declarations.

      - **Restrictive** (default ``False``): things only some environments
        provide (blocking, UI dialogs, screen capture).  A command
        declares a need by setting the field ``True``; an environment
        advertises availability the same way.

    **Adding a capability** is adding a field below with a ``# What:`` /
    ``# Where:`` comment block, declared with ``_restricted`` unless every
    environment provides it.  Choose the default based on whether
    every termapy environment can provide it or not.
    """

    # ── Baseline (default True) ──────────────────────────────────────────
    # Every termapy environment provides these.  Listed explicitly so that
    # (a) readers can see the full contract, and (b) a restricted
    # environment can selectively opt out.

    # What:  ``ctx.write`` / ``ctx.write_markup`` write to a visible sink
    #        (terminal, log, or captured buffer).
    # Where: CLI, TUI, script runner, test harness.
    terminal_output: bool = True

    # What:  ``ctx.serial_write`` / ``ctx.serial_send`` / ``ctx.serial_*``
    #        can talk to the serial engine.  Note: being *connected* to a
    #        port is the ``serial_connected`` capability below -- this one
    #        only says the API is wired up.
    # Where: CLI, TUI, script runner.
    serial_io: bool = True

    # What:  ``ctx.dispatch(cmd)`` routes a command through the full
    #        dispatch pipeline (directives, transforms, REPL/serial).
    # Where: Every environment; scripts nest dispatch heavily.
    dispatch: bool = True

    # What:  ``ctx.cfg``, ``ctx.config_path``, and the folder paths
    #        (``scripts_dir``, ``proto_dir``, ...) are populated and
    #        readable.
    # Where: Every environment.  Tests sometimes use a synthetic config.
    #        Note: having a config *loaded* is the ``config_loaded``
    #        capability below -- this one only says the fields are
    #        wired up (``config_path`` is ``""`` when none is loaded).
    config_read: bool = True

    # ── Restrictive (default False) ──────────────────────────────────────
    # Only some environments provide these.  Commands opt in by setting
    # ``needs=CapabilitySet(<name>=True)``; environments opt in by
    # setting the same field in their ``ctx.capabilities``.  Each is
    # declared with ``_restricted``, which carries its /help hint.

    # What:  Handler can block the calling thread waiting for serial input
    #        or a user response (e.g. /expect, /confirm).
    # Where: Script runner only.  Blocking at the REPL would freeze the
    #        TUI's event loop; blocking in the CLI event path would hang
    #        stdin echo.  The script runner already executes on a
    #        background worker thread that it's safe to block.
    block_until: bool = _restricted("a thread that may block (.run scripts only)", attainable=True)

    # What:  ``ctx.ui.confirm(message)`` asks Yes/Cancel and returns the
    #        user's answer synchronously.
    # Where: TUI (a dialog) and CLI (a y/n prompt on stdin; a piped run
    #        reads No).  Not MCP.  Implies block_until (the handler stops
    #        until the user answers).
    confirm_dialog: bool = _restricted("a Yes/Cancel prompt (the TUI's dialog, the CLI's y/n)")

    # What:  ``ctx.notify(text)`` shows a transient toast-style message
    #        that does not pollute the main output stream.
    # Where: TUI only.
    ui_notify: bool = _restricted("the TUI's toast notifications")

    # What:  ``ctx.status_bar(text, timeout)`` updates the bottom-of-screen
    #        status line.  No-op elsewhere.
    # Where: TUI only.
    status_bar: bool = _restricted("the TUI's status line")

    # What:  Can capture the rendered screen (``save_screenshot``,
    #        ``get_screen_text``).  Requires a graphical render surface.
    # Where: TUI only.  CLI renders to a plain terminal; there is no
    #        serialized screen state to capture.
    screen_capture: bool = _restricted("the TUI's render surface (screenshots, screen text)")

    # What:  Running inside the TUI (Textual) rather than the CLI.
    #        Commands that tweak TUI-specific display settings (line
    #        numbers, scrollback rendering, modal dialogs) declare this.
    # Where: TUI only.  Distinct from ``screen_capture`` -- that's about
    #        *reading* the render surface; this is about *using* TUI-only
    #        features at runtime.
    tui_mode: bool = _restricted("the TUI (switch with /tui)")

    # What:  A config is loaded: ``ctx.config_path`` names its file, so
    #        the config's folder and the data folders under it are
    #        there to read and write.
    # Where: Dynamic -- evaluated per dispatch from ``ctx.config_path``.
    #        Any environment once a config is loaded; the zero-config
    #        CLI and the TUI's startup picker begin without one.
    #        Commands that work in the config's folder (/run.list,
    #        /cfg.info, /sym.import, ...) declare this need so dispatch
    #        answers "No config loaded." instead of each handler
    #        re-implementing the check.
    config_loaded: bool = _restricted(
        "a loaded config",
        state="No config loaded.",
        attainable=True,
    )

    # What:  A serial port is currently open and transmitting.
    # Where: Dynamic -- evaluated per dispatch by checking
    #        ``ctx.is_connected()``.  Any environment can publish this,
    #        but only when a port is actually open.  Commands that send
    #        bytes (/proto.send, /xfer.xmodem.send, ...) declare this need
    #        so dispatch gives a clear "not connected" error instead of
    #        each handler re-implementing the check.
    serial_connected: bool = _restricted(
        "an open serial port",
        state="Not connected.",
        attainable=True,
    )

    # What:  An interactive session -- a human at a terminal with
    #        persistent scrollback, modal dialog support, or in-band UI
    #        chrome (mode switch, screen clear, line numbers).  Also
    #        the home of legacy aliases retained for human typing
    #        convenience (``/echo`` -> ``/term.echo``).
    # Where: TUI, CLI (whether running locally or over SSH).  Not MCP --
    #        an LLM client has no interactive session.
    interactive: bool = _restricted("an interactive session (a human at a terminal; not MCP)")

    # What:  Host can launch external desktop apps the user must visually
    #        see -- system editor, file viewer, browser, file explorer.
    #        Distinct from ``interactive`` because an SSH user IS
    #        interactive but has no local display, so calls like
    #        ``webbrowser.open()`` succeed silently while opening on
    #        the remote machine the user can't see.
    # Where: TUI / CLI when running locally on a graphical desktop.
    #        Detected at host startup via env vars; users can override
    #        with ``TERMAPY_GUI=1`` / ``TERMAPY_GUI=0``.  Not MCP.
    gui_apps: bool = _restricted("a local desktop that can open apps the user sees")

    # What:  Can open a project file for editing (``ctx.internal.edit_file``).
    #        The TUI opens its own editors for a config, a .run or a .pro
    #        file, and hands anything else to the system editor; the CLI
    #        has only the system editor.  One /edit command for both
    #        (review 2026-10-15, D5): the frontend decides how to open.
    # Where: TUI always (its editors work over SSH); CLI when ``gui_apps``.
    #        Not MCP.
    file_editor: bool = _restricted("an editor for project files (the TUI's own, or a desktop's)")

    # What:  The frontend keeps a session log of its own
    #        (``ctx.internal.session_log_path``): the TUI's ``<cfg>.log``
    #        (or ``log_file``), the MCP server's ``mcp/session.log``.  It is
    #        per config, so commands pair it with ``config_loaded``
    #        (``CapabilitySet.SESSION_LOG``).  Replaced five handler checks
    #        that answered "No log file." in the CLI, where /help said the
    #        /log commands were available (2026-10-16).
    # Where: TUI, MCP.  Not the CLI, which keeps none (review 2026-10-15, D3).
    session_log: bool = _restricted(
        "a session log (the TUI's or the MCP server's; the CLI keeps none)",
    )

    # What:  Handler may resolve a caller-supplied path OUTSIDE the
    #        per-config sandbox -- an absolute path or ``..`` traversal
    #        (e.g. ``/cap.text file=<abs>``, ``/profile.save <abs>``,
    #        ``/log.dump`` of a ``log_file`` outside the cfg dir).  When
    #        absent, ``ctx.fs.resolve`` contains names to the folder and
    #        refuses escapes.  Confines an automated peer to the config
    #        sandbox without touching device control.
    # Where: CLI, TUI (the operator IS the caller, on their own machine).
    #        MCP only when ``TERMAPY_MCP_FS_UNCONFINED`` is set.
    filesystem_unconfined: bool = _restricted(
        "host-wide file access (TERMAPY_MCP_FS_UNCONFINED=1 under MCP)",
    )

    # What:  Handler may open a NETWORK connection -- pyserial
    #        serial-over-URL "ports" (``socket://``, ``rfc2217://``) via
    #        ``/port.connect``.  When absent, a URL port is refused so an
    #        automated peer can't open arbitrary outbound TCP and send data
    #        off the machine; physical device ports are unaffected.
    # Where: CLI, TUI.  MCP only when ``TERMAPY_MCP_NET_EGRESS`` is set.
    network_egress: bool = _restricted(
        "outbound network access (TERMAPY_MCP_NET_EGRESS=1 under MCP)",
    )

    # ── Named profiles ──────────────────────────────────────────────────
    # The recurring ``needs=`` declarations, one frozen shared instance
    # each (assigned right after the class; safe because the dataclass is
    # frozen).  Use these for the common cases; spell out an unusual
    # combination inline.  Editors show each docstring on hover.

    SERIAL_CONNECTED: ClassVar[CapabilitySet]
    """Needs an OPEN serial port (``serial_connected=True``).

    The command sends or reads bytes; dispatch answers ``Not connected.``
    before the handler runs instead of every handler re-checking."""

    CONFIG_LOADED: ClassVar[CapabilitySet]
    """Needs a LOADED config (``config_loaded=True``).

    The command works in the config's folder (its data folders, sidecars,
    report); dispatch answers ``No config loaded.`` before the handler
    runs instead of every handler re-checking."""

    INTERACTIVE: ClassVar[CapabilitySet]
    """Needs a human at an interactive session (``interactive=True``).

    Persistent scrollback, modal dialogs, in-band UI chrome.  TUI and CLI
    (local or over SSH) provide it; MCP does not -- an LLM client has no
    interactive session."""

    GUI_APPS: ClassVar[CapabilitySet]
    """Can launch desktop apps the user can SEE (``gui_apps=True``).

    System editor, file viewer, browser.  Distinct from INTERACTIVE: an
    SSH user is interactive but has no local display, so a
    ``webbrowser.open()`` would "succeed" invisibly on the remote box.
    Detected at startup; ``TERMAPY_GUI=1/0`` overrides."""

    FILE_EDITOR: ClassVar[CapabilitySet]
    """Can open a project file for editing (``file_editor=True``).

    The TUI's own config and script editors, or a desktop's system
    editor.  The TUI has it over SSH too; the CLI only with a desktop."""

    SESSION_LOG: ClassVar[CapabilitySet]
    """This frontend's session log, which needs a config (``session_log`` + ``config_loaded``).

    The TUI and the MCP server keep one per config; the CLI keeps none,
    so the /log commands are unavailable there."""

    SCREEN_CAPTURE: ClassVar[CapabilitySet]
    """Can capture the rendered screen (``screen_capture=True``).

    Screenshots and screen text need a graphical render surface -- TUI
    only; the CLI has no serialized screen state."""

    TUI_MODE: ClassVar[CapabilitySet]
    """Uses TUI-only runtime features (``tui_mode=True``).

    Line numbers, scrollback rendering, modal screens.  Distinct from
    SCREEN_CAPTURE: that reads the render surface, this drives it."""

    BLOCK_UNTIL: ClassVar[CapabilitySet]
    """May block its thread waiting on the device (``block_until=True``).

    Script runner only: it already executes on a background worker that
    is safe to block; blocking at the REPL would freeze the TUI's event
    loop."""

    SERIAL_INTERACTIVE: ClassVar[CapabilitySet]
    """Needs an open port AND a human present (file transfers).

    ``serial_connected=True, interactive=True`` -- an XMODEM/YMODEM run
    holds the port and needs someone watching the progress."""

    CONFIG_GUI_APPS: ClassVar[CapabilitySet]
    """Opens the loaded config's files in a desktop app the user SEES.

    ``config_loaded=True, gui_apps=True`` -- the ``.explore`` and
    ``.show`` commands: a folder or file of the config, opened in the
    file explorer or the system viewer."""

    def satisfied_by(self, provided: CapabilitySet) -> bool:
        """True iff every capability set in ``self`` is also set in ``provided``."""
        return all(not getattr(self, f.name) or getattr(provided, f.name) for f in fields(self))

    def missing_from(self, provided: CapabilitySet) -> list[str]:
        """Return field names required by ``self`` that ``provided`` lacks.

        Order matches declaration order above, which is a stable, reviewable
        vocabulary (not alphabetical).
        """
        return [
            f.name for f in fields(self) if getattr(self, f.name) and not getattr(provided, f.name)
        ]

    def union(self, other: CapabilitySet) -> CapabilitySet:
        """Return a new set that has every capability provided by either side.

        Useful when deriving one environment from another, e.g. the script
        runner's capabilities are the REPL's plus ``block_until``.
        """
        return CapabilitySet(
            **{f.name: getattr(self, f.name) or getattr(other, f.name) for f in fields(self)}
        )


# The named profiles declared as ClassVars above.  Assigned here because a
# class body cannot reference the class it is defining.
CapabilitySet.SERIAL_CONNECTED = CapabilitySet(serial_connected=True)
CapabilitySet.CONFIG_LOADED = CapabilitySet(config_loaded=True)
CapabilitySet.INTERACTIVE = CapabilitySet(interactive=True)
CapabilitySet.GUI_APPS = CapabilitySet(gui_apps=True)
CapabilitySet.FILE_EDITOR = CapabilitySet(file_editor=True)
CapabilitySet.SESSION_LOG = CapabilitySet(session_log=True, config_loaded=True)
CapabilitySet.SCREEN_CAPTURE = CapabilitySet(screen_capture=True)
CapabilitySet.TUI_MODE = CapabilitySet(tui_mode=True)
CapabilitySet.BLOCK_UNTIL = CapabilitySet(block_until=True)
CapabilitySet.SERIAL_INTERACTIVE = CapabilitySet(serial_connected=True, interactive=True)
CapabilitySet.CONFIG_GUI_APPS = CapabilitySet(config_loaded=True, gui_apps=True)


# ── Views of the fields ──────────────────────────────────────────────────────
# Computed from the ``_restricted`` declarations above, the one place each
# fact is written (registry ``capability-declared-once``).  Baseline fields
# carry none of them: every environment provides those, so they would be
# noise in a requirements listing.

# /help's REQUIRED CAPABILITIES rows: one noun phrase per restrictive field,
# completing "requires ...".
REQUIREMENT_HINTS: dict[str, str] = {
    spec.name: spec.metadata["hint"] for spec in fields(CapabilitySet) if "hint" in spec.metadata
}

# The dynamic fields that are session STATE the user can change, not limits
# of the environment.  When they are all a command lacks, dispatch answers
# with the state rather than "requires: ... (not available in this
# environment)".  Declaration order is the gate's order: with neither, load a
# config first (it may open the port too), so ``config_loaded`` is declared
# before ``serial_connected``.  Registries ``state-answered-by-gate`` and
# ``state-messages-one-owner``.
STATE_MESSAGES: dict[str, str] = {
    spec.name: spec.metadata["state"]
    for spec in fields(CapabilitySet)
    if spec.metadata.get("state")
}

# The fields any frontend can reach during a session: a config (/cfg.load),
# a port (/port.connect), a blocking-safe thread (.run scripts under TUI/CLI;
# the MCP host grants it outright).  The dispatch gate reads them live
# (``ReplEngine._effective_capabilities``); whatever answers "can this
# command EVER run here" -- /help's AVAILABLE row, ``/help --mcp``, the MCP
# catalog -- grants them through ``attainable``.
ATTAINABLE: tuple[str, ...] = tuple(
    spec.name for spec in fields(CapabilitySet) if spec.metadata.get("attainable")
)


def attainable(caps: CapabilitySet) -> CapabilitySet:
    """``caps`` with every dynamic capability granted: what it could EVER do.

    ``replace``, not ``union``: a fresh ``CapabilitySet`` has every baseline
    True, so a union would also re-grant a baseline ``caps`` turned off.
    """
    return replace(caps, **dict.fromkeys(ATTAINABLE, True))


# ─────────────────────────────────────────────────────────────────────────────
# GUI-apps detection + environment capability sets
# ─────────────────────────────────────────────────────────────────────────────


def detect_gui_apps() -> bool:
    """Heuristic: can this process launch external desktop apps the user sees?

    SSH users have an *interactive* session but typically no local desktop --
    ``webbrowser.open()`` and ``os.startfile()`` succeed silently while
    opening on the remote machine.  This detector lets hosts advertise
    ``gui_apps`` correctly so commands like ``/help.open`` and ``/edit``
    are gated when they wouldn't actually be useful.

    Detection order:

      1. ``TERMAPY_GUI`` env override, any boolean token (read once at
         startup by ``env_flags``; a bad value refuses to start).  Escape
         hatch for users whose environment fools the heuristic (mosh,
         tmux-in-SSH, WSLg, X2Go, ...).
      2. SSH session (``SSH_CONNECTION`` or ``SSH_TTY`` set): True only if
         X11 forwarding is also configured (``DISPLAY`` set).
      3. Linux / macOS: True iff ``DISPLAY`` or ``WAYLAND_DISPLAY`` is set.
      4. Windows: True (assume native graphical session).
      5. Unknown platform: False (fail safe).

    The heuristic is best-effort, not authoritative.  Real-world environments
    that fool it are handled by the override.

    Returns:
        True if external desktop apps will likely be visible to the user.
    """
    from termapy import env_flags

    if env_flags.GUI_OVERRIDE is not None:
        return env_flags.GUI_OVERRIDE
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return bool(os.environ.get("DISPLAY"))
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if sys.platform == "win32":
        return True
    return False


def _build_environments() -> dict[str, CapabilitySet]:
    """Build the per-environment capability sets used by ``/help`` rendering.

    The single source of truth for "where can this command run."  The
    ``AVAILABLE`` row in the man page and ``/help --mcp`` both derive from
    this map.  Adding a new host (web preview, CI runner, ...) means adding
    a row here.

    ``gui_apps`` is computed once at import time via :func:`detect_gui_apps`.
    Long-running servers don't change desktop state mid-process; users who
    SSH in and out and reload termapy get fresh detection.
    """
    gui = detect_gui_apps()
    # MCP host confinement is per-class opt-in from the server's shell;
    # mirror it here so /help AVAILABLE reflects the running policy.
    from termapy.env_flags import MCP_FS_UNCONFINED, MCP_NET_EGRESS

    environments = {
        # TUI (Textual app): everything an interactive desktop terminal has.
        "TUI": CapabilitySet(
            interactive=True,
            gui_apps=gui,
            file_editor=True,
            session_log=True,
            tui_mode=True,
            screen_capture=True,
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            filesystem_unconfined=True,
            network_egress=True,
        ),
        # CLI (interactive REPL or --run script): interactive but no Textual UI.
        # Its confirmation is the y/n prompt on stdin.
        "CLI": CapabilitySet(
            interactive=True,
            gui_apps=gui,
            file_editor=gui,
            confirm_dialog=True,
            filesystem_unconfined=True,
            network_egress=True,
        ),
        # MCP (stdio server): no human, no display.  Host access beyond the
        # device is sandboxed unless the operator opts in per-class.
        "MCP": CapabilitySet(
            session_log=True,
            filesystem_unconfined=MCP_FS_UNCONFINED,
            network_egress=MCP_NET_EGRESS,
        ),
    }
    # This matrix answers "can the command EVER run there"; "is it
    # available RIGHT NOW" is the REQUIRED CAPABILITIES section's job (its
    # "(missing)" marker).  Without the dynamic capabilities every serial
    # command rendered "TUI: no  CLI: no  MCP: no" -- statically wrong.
    return {name: attainable(caps) for name, caps in environments.items()}


ENVIRONMENTS: dict[str, CapabilitySet] = _build_environments()
