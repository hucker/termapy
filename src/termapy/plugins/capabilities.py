"""Capability model for the plugin system.

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

  1. Add a new field here with a ``bool = False`` default and a comment
     explaining *what* it means and *where* it's provided.
  2. The environments that provide it set the field to True when they
     build ``ctx.capabilities`` (typically in ``app.py`` / ``cli.py`` /
     the script runner in ``repl.py``).
  3. Commands that require it set the field in their ``Command.needs``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields


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
    ``# Where:`` comment block.  Choose the default based on whether
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
    config_read: bool = True

    # ── Restrictive (default False) ──────────────────────────────────────
    # Only some environments provide these.  Commands opt in by setting
    # ``needs=CapabilitySet(<name>=True)``; environments opt in by
    # setting the same field in their ``ctx.capabilities``.

    # What:  Handler can block the calling thread waiting for serial input
    #        or a user response (e.g. /expect, /confirm).
    # Where: Script runner only.  Blocking at the REPL would freeze the
    #        TUI's event loop; blocking in the CLI event path would hang
    #        stdin echo.  The script runner already executes on a
    #        background worker thread that it's safe to block.
    block_until: bool = False

    # What:  ``ctx.confirm(message)`` can show a real Yes/Cancel dialog
    #        and return the user's answer synchronously.
    # Where: TUI + script runner.  Implies block_until (the handler stops
    #        until the user answers).  CLI has no dialog today, though a
    #        text-mode prompt could provide this later.
    confirm_dialog: bool = False

    # What:  ``ctx.notify(text)`` shows a transient toast-style message
    #        that does not pollute the main output stream.
    # Where: TUI only.
    ui_notify: bool = False

    # What:  ``ctx.status_bar(text, timeout)`` updates the bottom-of-screen
    #        status line.  No-op elsewhere.
    # Where: TUI only.
    status_bar: bool = False

    # What:  Can capture the rendered screen (``save_screenshot``,
    #        ``get_screen_text``).  Requires a graphical render surface.
    # Where: TUI only.  CLI renders to a plain terminal; there is no
    #        serialized screen state to capture.
    screen_capture: bool = False

    # What:  Running inside the TUI (Textual) rather than the CLI.
    #        Commands that tweak TUI-specific display settings (line
    #        numbers, scrollback rendering, modal dialogs) declare this.
    # Where: TUI only.  Distinct from ``screen_capture`` -- that's about
    #        *reading* the render surface; this is about *using* TUI-only
    #        features at runtime.
    tui_mode: bool = False

    # What:  A serial port is currently open and transmitting.
    # Where: Dynamic -- evaluated per dispatch by checking
    #        ``ctx.is_connected()``.  Any environment can publish this,
    #        but only when a port is actually open.  Commands that send
    #        bytes (/proto.send, /xfer.xmodem.send, ...) declare this need
    #        so dispatch gives a clear "not connected" error instead of
    #        each handler re-implementing the check.
    serial_connected: bool = False

    # What:  An interactive session -- a human at a terminal with
    #        persistent scrollback, modal dialog support, or in-band UI
    #        chrome (mode switch, screen clear, line numbers).  Also
    #        the home of legacy aliases retained for human typing
    #        convenience (``/echo`` -> ``/term.echo``).
    # Where: TUI, CLI (whether running locally or over SSH).  Not MCP --
    #        an LLM client has no interactive session.
    interactive: bool = False

    # What:  Host can launch external desktop apps the user must visually
    #        see -- system editor, file viewer, browser, file explorer.
    #        Distinct from ``interactive`` because an SSH user IS
    #        interactive but has no local display, so calls like
    #        ``webbrowser.open()`` succeed silently while opening on
    #        the remote machine the user can't see.
    # Where: TUI / CLI when running locally on a graphical desktop.
    #        Detected at host startup via env vars; users can override
    #        with ``TERMAPY_GUI=1`` / ``TERMAPY_GUI=0``.  Not MCP.
    gui_apps: bool = False

    # What:  Handler may resolve a caller-supplied path OUTSIDE the
    #        per-config sandbox -- an absolute path or ``..`` traversal
    #        (e.g. ``/cap.text file=<abs>``, ``/profile.save <abs>``,
    #        ``/log.dump`` of a ``log_file`` outside the cfg dir).  When
    #        absent, ``ctx.fs.resolve`` contains names to the folder and
    #        refuses escapes.  Confines an automated peer to the config
    #        sandbox without touching device control.
    # Where: CLI, TUI (the operator IS the caller, on their own machine).
    #        MCP only when ``TERMAPY_MCP_FS_UNCONFINED`` is set.
    filesystem_unconfined: bool = False

    # What:  Handler may open a NETWORK connection -- pyserial
    #        serial-over-URL "ports" (``socket://``, ``rfc2217://``) via
    #        ``/port.connect``.  When absent, a URL port is refused so an
    #        automated peer can't open arbitrary outbound TCP (egress /
    #        exfil); physical device ports are unaffected.
    # Where: CLI, TUI.  MCP only when ``TERMAPY_MCP_NET_EGRESS`` is set.
    network_egress: bool = False

    def satisfied_by(self, provided: "CapabilitySet") -> bool:
        """True iff every capability set in ``self`` is also set in ``provided``."""
        return all(
            not getattr(self, f.name) or getattr(provided, f.name) for f in fields(self)
        )

    def missing_from(self, provided: "CapabilitySet") -> list[str]:
        """Return field names required by ``self`` that ``provided`` lacks.

        Order matches declaration order above, which is a stable, reviewable
        vocabulary (not alphabetical).
        """
        return [
            f.name
            for f in fields(self)
            if getattr(self, f.name) and not getattr(provided, f.name)
        ]

    def union(self, other: "CapabilitySet") -> "CapabilitySet":
        """Return a new set that has every capability provided by either side.

        Useful when deriving one environment from another, e.g. the script
        runner's capabilities are the REPL's plus ``block_until``.
        """
        return CapabilitySet(
            **{
                f.name: getattr(self, f.name) or getattr(other, f.name)
                for f in fields(self)
            }
        )


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

      1. ``TERMAPY_GUI`` env override (``1``/``yes``/``true``/``on`` -> True;
         ``0``/``no``/``false``/``off`` -> False).  Escape hatch for users
         whose environment fools the heuristic (mosh, tmux-in-SSH, WSLg,
         X2Go, ...).
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
    override = os.environ.get("TERMAPY_GUI", "").strip().lower()
    if override in ("1", "yes", "true", "on"):
        return True
    if override in ("0", "no", "false", "off"):
        return False
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return bool(os.environ.get("DISPLAY"))
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        return bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )
    if sys.platform == "win32":
        return True
    return False


def _build_environments() -> dict[str, "CapabilitySet"]:
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

    return {
        # TUI (Textual app): everything an interactive desktop terminal has.
        "TUI": CapabilitySet(
            interactive=True,
            gui_apps=gui,
            tui_mode=True,
            screen_capture=True,
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            filesystem_unconfined=True,
            network_egress=True,
        ),
        # CLI (interactive REPL or --run script): interactive but no Textual UI.
        "CLI": CapabilitySet(
            interactive=True,
            gui_apps=gui,
            filesystem_unconfined=True,
            network_egress=True,
        ),
        # MCP (stdio server): no human, no display.  Host access beyond the
        # device is sandboxed unless the operator opts in per-class.
        "MCP": CapabilitySet(
            filesystem_unconfined=MCP_FS_UNCONFINED,
            network_egress=MCP_NET_EGRESS,
        ),
    }


ENVIRONMENTS: dict[str, "CapabilitySet"] = _build_environments()
