"""UIHandle -- TUI-strict operations that require the right environment.

Reachable as ``ctx.ui.*``.  Every method here checks a capability
and raises :class:`MissingCapability` if the host can't actually
deliver it.  The contrast with :class:`IOHandle`:

  - ``ctx.io.notify("hi")`` always works (CLI prints ``[notice] hi``;
    TUI shows a toast).  Use this when you want the user to see
    something and don't care which sink delivers it.
  - ``ctx.ui.notify("hi")`` raises in CLI; only succeeds in TUI.
    Use this when "this MUST be a real toast" matters -- for
    example, a dialog command that's meaningful only in TUI.

Plugins that call ``ctx.ui.*`` should declare the corresponding
capability in their ``Command(needs=CapabilitySet(...))`` so the
dispatcher can refuse to run them in unsupported environments
*before* the handler code runs.  Without a declaration, the gate
fires at the call site as a backstop.

Method-to-capability map:

  - ``confirm``           -> ``confirm_dialog``  (also implies ``block_until``)
  - ``notify``            -> ``ui_notify``
  - ``status_bar``        -> ``status_bar``
  - ``clear_screen``      -> ``tui_mode``
  - ``screenshot``        -> ``screen_capture``
  - ``get_screen_text``   -> ``screen_capture``
  - ``exit_app``          -> ``tui_mode``
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins.capabilities import MissingCapability

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


class UIHandle:
    """TUI-strict operations: confirm dialog, notifications, screenshots, exit."""

    def __init__(self, ctx: "PluginContext") -> None:
        self._ctx = ctx

    def _require(self, flag: str, method: str) -> None:
        """Raise MissingCapability if the host doesn't provide ``flag``."""
        if not getattr(self._ctx.capabilities, flag, False):
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
        return self._ctx.confirm(message)

    # ── Toast / status bar / clear screen (TUI-strict variants) ──────

    def notify(self, text: str, **kw) -> None:
        """Show a real Textual toast.  Use ``ctx.io.notify`` for the always-works fallback.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``ui_notify``.
        """
        self._require("ui_notify", "notify")
        self._ctx.notify(text, **kw)

    def status_bar(self, text: str, timeout: float = 5.0) -> None:
        """Set transient text in the bottom status line.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``status_bar``.
        """
        self._require("status_bar", "status_bar")
        self._ctx.status_bar(text, timeout)

    def clear_screen(self) -> None:
        """Clear the TUI's scrollback and reset the line counter.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``tui_mode``.
        """
        self._require("tui_mode", "clear_screen")
        self._ctx.clear_screen()

    # ── Screen capture ───────────────────────────────────────────────

    def screenshot(self, path) -> None:
        """Save the current TUI render surface to ``path``.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``screen_capture``.
        """
        self._require("screen_capture", "screenshot")
        self._ctx.save_screenshot(path)

    def get_screen_text(self) -> str:
        """Return all visible TUI output as plain text.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``screen_capture``.
        """
        self._require("screen_capture", "get_screen_text")
        return self._ctx.get_screen_text()

    # ── App lifecycle ────────────────────────────────────────────────

    def exit_app(self) -> None:
        """Exit the TUI cleanly.

        Raises:
            MissingCapability: if the environment doesn't provide
                ``tui_mode``.
        """
        self._require("tui_mode", "exit_app")
        self._ctx.exit_app()
