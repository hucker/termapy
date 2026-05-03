"""Built-in plugin: /credits -- print the acknowledgments page.

Single source of truth lives at
``src/termapy/help/acknowledgments.md`` -- bundled with the package
via the ``[tool.uv-build] include`` rule on ``src/termapy/help/*.md``.
The repo-root view is the README link; this command surfaces the
same content in-terminal.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _acknowledgments_path() -> Path:
    """Return the path to the bundled acknowledgments.md."""
    # Lives next to the help/ markdown siblings so zensical renders it
    # into the docs site too.
    return Path(__file__).resolve().parent.parent.parent / "help" / "acknowledgments.md"


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Print the acknowledgments / attribution page to the terminal."""
    path = _acknowledgments_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return CmdResult.fail(msg=f"Could not read acknowledgments: {e}")
    for line in text.splitlines():
        ctx.output(line)
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="credits",
    help="Show acknowledgments / third-party attributions.",
    long_help=(
        "Prints the acknowledgments page -- every runtime dependency,\n"
        "every vendored package, and the authors behind them.\n"
        "\n"
        "The canonical source file is ``src/termapy/help/acknowledgments.md``."
    ),
    handler=_handler,
    mcp_visible=False,  # display-only; not useful for LLM-driven invocation
)
