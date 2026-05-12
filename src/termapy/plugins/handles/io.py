"""IOHandle -- all output-to-user operations.

Reachable as ``ctx.io.*``.  Domain: writing to terminal, log file,
notifications, status bar, screen-clear.  These are the universally
available output sinks; the TUI-strict variants of ``notify`` /
``status_bar`` / ``clear_screen`` live on :class:`UIHandle`.

**Handler-facing API:** plugin handlers should call ``result(text)``,
``output(text)``, or ``status(text)`` -- the three semantic channels
that respect the ``silent``/``quiet``/``normal``/``verbose`` level
dial.  Errors flow through ``CmdResult.fail(msg=...)`` and the
dispatcher paints the red line.  The lower-level ``_write`` /
``_write_markup`` primitives bypass level gating and are reserved
for engine-internal use (dispatcher red-line prints, the silent-
mode shim).  A CI grep guard fails the build if a builtin calls
either of the underscored primitives directly.

Self-contained dataclass: every operation is a callable field that
the host wires at construction time.  The level-routed methods
(``result``, ``output``, ``status``) read the current output level
via ``output_level_fn`` -- a callable supplied at construction (or
set by ``PluginContext.__post_init__``) that returns the current
level.  This is how IOHandle stays self-contained while still
respecting the verbose/quiet/silent dial.

This handle is **not** capability-gated.  ``terminal_output`` is
baseline-True and every shipped environment provides it; the
dispatcher's ``Command.needs.satisfied_by`` check is sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from termapy.plugins.output_levels import (
    DEFAULT_OUTPUT_LEVEL,
    OUTPUT_LEVEL_RANK,
    OUTPUT_MIN_RANK,
    RESULT_MIN_RANK,
    STATUS_MIN_RANK,
)


@dataclass
class IOHandle:
    """Output operations: write/markup/log/result/output/status/notify/status_bar/clear_screen.

    Always-works fallback semantics for ``notify``, ``status_bar``, and
    ``clear_screen`` -- they degrade gracefully in non-TUI environments
    (CLI prints ``[notice] text``, MCP no-ops).  Use :class:`UIHandle`
    for TUI-strict variants that raise ``MissingCapability`` when the
    environment can't actually deliver them.
    """

    # ── Plain-text output (engine-internal primitives) ───────────────
    # Underscore signals "private to engine/dispatcher use."  Handler
    # code should not call these directly -- use ``result``/``output``/
    # ``status`` instead.  A CI grep guard enforces this in
    # ``src/termapy/builtins/``.
    _write: Callable = lambda text, color="dim": None
    _write_markup: Callable = lambda text: None
    log: Callable = lambda prefix, text: None

    # ── Always-works fallbacks for TUI features ──────────────────────
    notify: Callable = lambda text, **kw: None
    status_bar: Callable = lambda text, timeout=5.0: None
    clear_screen: Callable = lambda: None

    # ── Output-level routing ─────────────────────────────────────────
    # Returns the current output level.  PluginContext sets this in
    # __post_init__ to a closure over its own ``output_level``
    # property so per-call overrides via ``cmd.quiet`` / ``cmd
    # --silent`` flow through.
    output_level_fn: Callable = lambda: DEFAULT_OUTPUT_LEVEL

    def _shows(self, min_rank: int) -> bool:
        rank = OUTPUT_LEVEL_RANK.get(
            self.output_level_fn(), OUTPUT_LEVEL_RANK[DEFAULT_OUTPUT_LEVEL]
        )
        return rank >= min_rank

    def result(self, text: str, color: str = "green") -> None:
        """Write a command result (single-line answer).  Shown at quiet+."""
        if self._shows(RESULT_MIN_RANK):
            self._write(text, color)

    def output(self, text: str, color: str = "dim") -> None:
        """Write data output (listings, dumps, file contents).  Shown at normal+."""
        if self._shows(OUTPUT_MIN_RANK):
            self._write(text, color)

    def status(self, text: str) -> None:
        """Write a status/progress message.  Shown only at verbose."""
        if self._shows(STATUS_MIN_RANK):
            self._write(text, "dim")
