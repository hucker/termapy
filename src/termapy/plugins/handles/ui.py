"""UIHandle -- TUI-strict operations that require the right environment.

Reachable as ``ctx.ui.*``.  Every gated method here checks a
capability and raises :class:`MissingCapability` if the host can't
actually deliver it -- contrast with :class:`IOHandle` which provides
always-works fallbacks for ``notify`` / ``status_bar`` /
``clear_screen``.

Self-contained dataclass: every operation is a callable field that
the host wires at construction time.  Capability gating reads from
the ``capabilities`` field (a snapshot of the host's CapabilitySet,
populated by ``PluginContext.__post_init__``).

Method-to-capability map:

  - ``confirm``           -> ``confirm_dialog`` (also implies ``block_until``)
  - ``notify``            -> ``ui_notify``
  - ``status_bar``        -> ``status_bar``
  - ``clear_screen``      -> ``tui_mode``
  - ``screenshot``        -> ``screen_capture``
  - ``get_screen_text``   -> ``screen_capture``
  - ``exit_app``          -> (not gated; underlying impl is a clean
                              exit in TUI and a no-op in CLI/MCP, so
                              always safe to call.  Commands that
                              genuinely need TUI still gate via
                              ``Command.needs=CapabilitySet(interactive=True)``)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from termapy.plugins.capabilities import CapabilitySet, MissingCapability


@dataclass
class UIHandle:
    """TUI-strict operations: confirm dialog, notifications, screenshots, exit."""

    # Backing callables.  Defaults are no-ops; hosts wire concrete
    # implementations.
    _confirm_impl: Callable = lambda message: False
    _notify_impl: Callable = lambda text, **kw: None
    _status_bar_impl: Callable = lambda text, timeout=5.0: None
    _clear_screen_impl: Callable = lambda: None
    _save_screenshot_impl: Callable = lambda path: None
    _get_screen_text_impl: Callable = lambda: ""
    _exit_app_impl: Callable = lambda: None

    # Capability snapshot for the gates.  PluginContext sets this in
    # __post_init__ to its own CapabilitySet.
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)

    def _require(self, flag: str, method: str) -> None:
        """Raise MissingCapability if the host doesn't provide ``flag``."""
        if not getattr(self.capabilities, flag, False):
            raise MissingCapability(
                f"ctx.ui.{method} requires {flag} capability; "
                f"declare needs=CapabilitySet({flag}=True) on your Command"
            )

    # ── Modal dialog ─────────────────────────────────────────────────

    def confirm(self, message: str) -> bool:
        """Show a Yes/Cancel dialog.  Blocks until the user answers.

        Must be called from a background worker thread (the script
        runner provides this; the REPL/CLI event loop does not).

        Raises:
            MissingCapability: if the environment doesn't provide
                ``confirm_dialog``.
        """
        self._require("confirm_dialog", "confirm")
        return self._confirm_impl(message)

    # ── Toast / status bar / clear screen (TUI-strict variants) ──────

    def notify(self, text: str, **kw) -> None:
        """Show a real Textual toast.  Use ``ctx.io.notify`` for the always-works fallback.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``ui_notify``.
        """
        self._require("ui_notify", "notify")
        self._notify_impl(text, **kw)

    def status_bar(self, text: str, timeout: float = 5.0) -> None:
        """Set transient text in the bottom status line.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``status_bar``.
        """
        self._require("status_bar", "status_bar")
        self._status_bar_impl(text, timeout)

    def clear_screen(self) -> None:
        """Clear the TUI's scrollback and reset the line counter.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``tui_mode``.
        """
        self._require("tui_mode", "clear_screen")
        self._clear_screen_impl()

    # ── Screen capture ───────────────────────────────────────────────

    def screenshot(self, path) -> None:
        """Save the current TUI render surface to ``path``.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``screen_capture``.
        """
        self._require("screen_capture", "screenshot")
        self._save_screenshot_impl(path)

    def get_screen_text(self) -> str:
        """Return all visible TUI output as plain text.

        In environments without screen capture (CLI, MCP) hosts wire
        the impl to return ``""``; ``/grep`` and similar handle that
        as "no scrollback here."  Not gated, because callers expect
        ``""`` as the no-op sentinel rather than ``MissingCapability``.
        """
        return self._get_screen_text_impl()

    # ── App lifecycle ────────────────────────────────────────────────

    def exit_app(self) -> None:
        """Exit the app.  No-op in CLI mode (the REPL loop handles ``/exit``).

        Not capability-gated at the handle level: the underlying
        impl is a no-op in CLI/MCP and a clean shutdown in TUI, so
        it's always safe to call.  Commands that genuinely need TUI
        semantics (e.g. ``/exit``) still declare
        ``needs=CapabilitySet(interactive=True)`` to keep the command
        out of MCP and other non-interactive environments.
        """
        self._exit_app_impl()
