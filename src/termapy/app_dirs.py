"""User-level application directories and files for termapy.

Per-project config lives in ``termapy_cfg/<name>/`` alongside the
user's work.  This module is the opposite end of the scale: **app-wide
state and config** that isn't tied to any single project.

Two concepts:

- **state** -- transient or machine-written data the user doesn't
  edit by hand (PyPI update-check timestamps, crash dumps, log
  files, caches).  Safe to delete; the app will regenerate.  Lives
  in ``app_state_dir() / state.json``.
- **config** -- persistent, user-editable global preferences that
  apply across all termapy projects.  Lives in
  ``app_config_dir() / config.json``.  Currently reserved (no
  features read from it yet).

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

import json
import os
from pathlib import Path

from platformdirs import user_config_dir, user_state_dir

_APP_NAME = "termapy"
_STATE_FILE_NAME = "state.json"
_CONFIG_FILE_NAME = "config.json"


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


def app_state_file() -> Path:
    """Return the path to the app-wide state JSON file.

    The file itself may not exist yet; callers should handle that
    via ``load_app_state()`` rather than reading directly.
    """
    return app_state_dir() / _STATE_FILE_NAME


def app_config_file() -> Path:
    """Return the path to the app-wide config JSON file.

    The file itself may not exist yet; callers should handle that
    via ``load_app_config()`` rather than reading directly.
    """
    return app_config_dir() / _CONFIG_FILE_NAME


def load_app_state() -> dict:
    """Load ``state.json`` or return ``{}`` on any error.

    Missing file, malformed JSON, permissions error -- all return
    an empty dict so callers get a clean slate rather than having
    to guard against I/O.
    """
    try:
        with app_state_file().open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_app_state(state: dict) -> None:
    """Write ``state.json``, swallowing any I/O error.

    App-wide state is non-critical; a failed save just means the
    next session starts from an older snapshot.  Not worth bothering
    the user about.
    """
    try:
        with app_state_file().open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def load_app_config() -> dict:
    """Load ``config.json`` or return ``{}`` on any error.

    See ``load_app_state`` for the error semantics -- same rules.
    """
    try:
        with app_config_file().open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
