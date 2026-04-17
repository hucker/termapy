import termapy.vendor  # noqa: F401 - register vendored pyserial before anything imports serial

# Import the CLI entry from termapy.entry, NOT termapy.app.  The entry
# module is Textual-free so ``termapy --ports`` (and friends) don't pay
# the ~300ms / 40MB cost of loading Textual for a print-and-exit run.
# Textual is imported lazily inside entry.main() when the user has
# actually asked for the TUI.
from .entry import main as run

__all__ = ["run"]
