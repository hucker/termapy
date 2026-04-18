"""Background PyPI update check for termapy.

Runs at most once per 7 days from the TUI (only), compares the
installed termapy version against the latest PyPI release, and
returns the latest version string when an upgrade is available.

Design contract: **any failure is silent**.  Network down, PyPI
returning garbage, state file corrupt, clock skew, unparseable
version string -- all of these make the check give up and return
None.  The user must never see an error from this feature.

The caller (see ``app.py``) runs this on a background thread and,
if the return value is non-None, writes a single multi-line banner
to the output window.  There is no dialog, no "ignore" state, no
install-method detection.  If the user is still out of date in 7
days, they see the banner again; if they upgraded, the version
check returns None naturally.

Intentional simplicity:

- No release-age gate.  A release that's up is a release to know
  about; chasing sprint thrashing is over-engineering.
- No ``notified_version`` tracking.  Every check either prints or
  doesn't; the 7-day ``last_checked`` gate is the only throttle.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packaging.version import InvalidVersion, Version

from termapy.app_dirs import app_state_dir

# Tunables -- module-level so tests can patch them cleanly.
_PYPI_JSON_URL = "https://pypi.org/pypi/termapy/json"
_HTTP_TIMEOUT_S = 2.0
_CHECK_INTERVAL_DAYS = 7
_STATE_FILE_NAME = "update_check.json"


def _state_path() -> Path:
    """Return the path to the update-check state JSON."""
    return app_state_dir() / _STATE_FILE_NAME


def _load_state() -> dict:
    """Load state JSON or return ``{}`` on any error.

    Missing file, malformed JSON, permissions error -- all return
    an empty dict so the caller treats this as "never checked."
    """
    try:
        with _state_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    """Write state JSON, swallowing any error.

    A failed save just means we'll re-check sooner than planned --
    not a reason to bother the user.
    """
    try:
        with _state_path().open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _now() -> datetime:
    """Return current UTC time.  Seam for test clock injection."""
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 string; return None on any failure."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _due_for_check(state: dict, now: datetime) -> bool:
    """Return True if >= _CHECK_INTERVAL_DAYS have passed since last check."""
    last = _parse_iso(state.get("last_checked", ""))
    if last is None:
        return True  # Never checked, or unparseable -- check now.
    return now - last >= timedelta(days=_CHECK_INTERVAL_DAYS)


def _fetch_pypi_latest() -> str | None:
    """Return the latest termapy version from PyPI, or None on any error.

    Uses stdlib ``urllib`` so we don't pull in ``requests``.  Any
    exception (network, HTTP non-200, JSON parse, missing fields)
    returns None.
    """
    try:
        req = urllib.request.Request(
            _PYPI_JSON_URL,
            headers={"Accept": "application/json", "User-Agent": "termapy"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["info"]["version"]
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ):
        return None


def _is_newer(latest: str, current: str) -> bool:
    """PEP 440 version compare.  Return False on any parse error."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def check(current_version: str) -> str | None:
    """Run one update check.  Return the latest version, or None.

    Bail-out rules (all return None):

    - Less than ``_CHECK_INTERVAL_DAYS`` since the last check.
    - PyPI unreachable or malformed response.
    - Latest version is not strictly greater than ``current_version``.
    - Any unexpected exception anywhere in the pipeline.

    The state file's ``last_checked`` timestamp is updated on every
    check attempt (success or network failure) so a flaky connection
    doesn't cause us to hit PyPI on every startup.
    """
    try:
        now = _now()
        state = _load_state()

        if not _due_for_check(state, now):
            return None

        latest = _fetch_pypi_latest()
        # Record the attempt regardless of success.
        state["last_checked"] = now.isoformat()
        _save_state(state)

        if latest is None:
            return None
        if not _is_newer(latest, current_version):
            return None
        return latest
    except Exception:
        # Catch-all guard: nothing from this module should ever propagate.
        return None
