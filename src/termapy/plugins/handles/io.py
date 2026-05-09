"""IOHandle -- all output-to-user operations.

Reachable as ``ctx.io.*``.  Domain: writing to terminal, log file,
notifications, status bar, screen-clear.  These are the universally
available output sinks; the TUI-strict variants of ``notify`` /
``status_bar`` / ``clear_screen`` live on :class:`UIHandle`.

The handle is a thin façade over :class:`PluginContext`'s flat fields.
Each method delegates to the corresponding ``ctx.<name>`` callable so
post-construction overrides (TUI's ``ctx.write = self._status``) are
picked up live.

This handle is **not** capability-gated -- the underlying capabilities
(``terminal_output`` for ``write``, etc.) are baseline-True and every
shipped environment provides them.  A restricted environment that
opted out of ``terminal_output`` would be gated at dispatch time, not
at handle-method time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


class IOHandle:
    """Output operations: write/markup/log/result/output/status/notify/status_bar/clear_screen.

    Always-works fallback semantics for ``notify``, ``status_bar``, and
    ``clear_screen`` -- they degrade gracefully in non-TUI environments
    (CLI prints ``[notice] text``, MCP no-ops).  Use :class:`UIHandle`
    for TUI-strict variants that raise ``MissingCapability`` when the
    environment can't actually deliver them.
    """

    def __init__(self, ctx: "PluginContext") -> None:
        self._ctx = ctx

    # ── Plain-text output (universal) ────────────────────────────────

    def write(self, text: str, color: str = "dim") -> None:
        """Write text to the terminal.  Color is a Rich color name."""
        self._ctx.write(text, color)

    def write_markup(self, text: str) -> None:
        """Write Rich markup text (supports ``[bold red]...[/]``)."""
        self._ctx.write_markup(text)

    def log(self, prefix: str, text: str) -> None:
        """Append a timestamped line to the session log.

        Prefix is ``">"`` for TX, ``"<"`` for RX, ``"#"`` for status.
        Independent of screen output -- always logged regardless of
        echo settings.
        """
        self._ctx.log(prefix, text)

    # ── Output-level routing (verbose-aware) ─────────────────────────

    def result(self, text: str, color: str = "green") -> None:
        """Write a command result (single-line answer).  Shown at quiet+."""
        self._ctx.result(text, color)

    def output(self, text: str, color: str = "dim") -> None:
        """Write data output (listings, dumps, file contents).  Shown at normal+."""
        self._ctx.output(text, color)

    def status(self, text: str) -> None:
        """Write a status/progress message.  Shown only at verbose."""
        self._ctx.status(text)

    # ── Always-works fallbacks for TUI features ──────────────────────
    # In CLI mode these print plain text; in TUI mode they show the
    # real toast / status bar / clear-screen.  Either way they always
    # produce something visible.  Plugins that need a guaranteed real
    # toast (or a hard refusal in CLI) use ``ctx.ui.notify`` instead.

    def notify(self, text: str, **kw) -> None:
        """Show a notification.  Real toast in TUI; ``[notice] text`` in CLI."""
        self._ctx.notify(text, **kw)

    def status_bar(self, text: str, timeout: float = 5.0) -> None:
        """Set transient text in the status bar.  No-op in CLI."""
        self._ctx.status_bar(text, timeout)

    def clear_screen(self) -> None:
        """Clear the terminal output.  TUI: clear scrollback; CLI: ANSI clear."""
        self._ctx.clear_screen()
