"""Detect how termapy was installed, for upgrade-command hints.

Looks at the running interpreter's ``sys.executable`` path for the
well-known directory shapes that uv tool / pipx / dev venvs leave
behind.  Falls back to a generic ``pip install -U termapy`` on no
match -- that works for plain pip and ``uv pip install`` setups,
which is most of the rest of the population.

Used by the cfg-validate "newer cfg, upgrade termapy" warning so
the user sees ONE command they can copy-paste, instead of the
full menu of possibilities.  Future callers (e.g. a ``/ver``
command showing install context) can reuse this without changes.
"""

from __future__ import annotations

import sys


def upgrade_command() -> str:
    """Return the most likely upgrade command for the running termapy.

    Detection is best-effort: it inspects ``sys.executable`` path
    fragments characteristic of common install layouts.  When
    nothing matches, returns the pip command -- a broadly-correct
    last resort.
    """
    exe = sys.executable.lower()

    # uv tool install: .../uv/tools/termapy/... on either OS.
    if "uv/tools/termapy" in exe.replace("\\", "/"):
        return "uv tool upgrade termapy"

    # pipx install: .../pipx/venvs/termapy/...
    if "pipx/venvs/termapy" in exe.replace("\\", "/"):
        return "pipx upgrade termapy"

    # Dev tree -- editable install in a project's .venv.  Upgrading
    # means pulling latest source, not chasing PyPI.
    if "/.venv/" in exe.replace("\\", "/"):
        return "git pull && uv pip install -e ."

    # Fallback -- works for plain pip and uv pip installs.
    return "pip install -U termapy"
