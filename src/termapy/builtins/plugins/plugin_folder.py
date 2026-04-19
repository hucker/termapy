"""Built-in plugin: /plugin folder operations.

Exposes the uniform folder subcommand family -- list, explore,
show, dump -- for the ``plugin/`` directory, so ``/plugin.list``
is the canonical way to see installed plugin files.  The root
``/plugin`` command itself has no handler (invoking bare ``/plugin``
lists its subcommands like any other parent command).

``plugin/`` is not ``clearable`` in ``FolderSpec`` -- plugin files
are user-authored and shouldn't be mass-deletable -- so ``.clear``
is deliberately absent.

The richer "list plugins by source" command is ``/help.plugin``;
this one just lists files on disk.
"""

from __future__ import annotations

from termapy.folder_ops import build_folder_subcommands
from termapy.plugins import Command


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="plugin",
    help="Plugin-folder tools: list, explore, show, dump.",
    sub_commands=build_folder_subcommands("plugin"),
)
