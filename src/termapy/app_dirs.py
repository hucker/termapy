"""User-level application directories for termapy.

Per-project config lives in ``termapy_cfg/<name>/`` alongside the
user's work.  This module is the opposite end of the scale: **app-wide
state and config** that isn't tied to any single project.

Use these for things that are cross-project and cross-session:

- ``app_state_dir()`` -- transient or machine-written state the user
  doesn't edit by hand (PyPI update-check timestamps, crash dumps,
  log files, caches).  Safe to delete; the app will regenerate.
- ``app_config_dir()`` -- persistent, user-editable global config
  (e.g. "always show update dialog: no").  Currently unused; reserved
  for future ``global.cfg``.

Resolved via ``platformdirs`` so paths follow each OS's convention:

- Windows: ``%LOCALAPPDATA%\\termapy\\`` (same dir for both state and
  config; Windows has no XDG-style split).
- macOS:   ``~/Library/Application Support/termapy/`` (config) and
           ``~/Library/Application Support/termapy/`` (state).
- Linux:   ``~/.config/termapy/`` (config) and
           ``~/.local/state/termapy/`` (state), honoring ``XDG_*``.

The env vars ``TERMAPY_STATE_DIR`` and ``TERMAPY_CONFIG_DIR`` override
the platform default per invocation -- handy for tests, CI runners,
and the occasional odd setup where the default location isn't
writable.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_state_dir

_APP_NAME = "termapy"


def app_state_dir() -> Path:
    """Return the directory for app-wide state; create it if missing.

    Honors ``TERMAPY_STATE_DIR`` for a full override.  Intended for
    machine-written state files the user doesn't edit: caches,
    timestamps, PyPI check markers, etc.
    """
    override = os.environ.get("TERMAPY_STATE_DIR")
    path = Path(override) if override else Path(
        user_state_dir(_APP_NAME, appauthor=False)
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def app_config_dir() -> Path:
    """Return the directory for app-wide user config; create it if missing.

    Honors ``TERMAPY_CONFIG_DIR`` for a full override.  Intended for
    persistent, user-editable global preferences that apply across
    all termapy projects.  Currently reserved for future use.
    """
    override = os.environ.get("TERMAPY_CONFIG_DIR")
    path = Path(override) if override else Path(
        user_config_dir(_APP_NAME, appauthor=False)
    )
    path.mkdir(parents=True, exist_ok=True)
    return path
