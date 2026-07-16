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

from packaging.version import InvalidVersion, Version

from termapy.app_dirs import load_app_state, save_app_state

# Tunables -- module-level so tests can patch them cleanly.
_PYPI_JSON_URL = "https://pypi.org/pypi/termapy/json"
_HTTP_TIMEOUT_S = 2.0
_CHECK_INTERVAL_DAYS = 7

# Subkey under which this feature stores its slice of state.json.
# Other features get their own subkeys; the file is a shared dict
# keyed by feature name so we don't scatter N small JSON files.
_STATE_KEY = "update_check"


def _load_state() -> dict:
    """Load this feature's slice of the shared app state file.

    Returns the ``update_check`` subkey of ``state.json``, or an
    empty dict if the file / subkey is missing or unreadable.
    """
    whole = load_app_state()
    slice_ = whole.get(_STATE_KEY)
    return slice_ if isinstance(slice_, dict) else {}


def _save_state(slice_: dict) -> None:
    """Persist this feature's slice back into the shared app state file.

    Reads the full ``state.json``, replaces the ``update_check``
    subkey, writes the whole thing back.  Other features' subkeys
    are preserved.
    """
    whole = load_app_state()
    whole[_STATE_KEY] = slice_
    save_app_state(whole)


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
    """Return True if a fresh PyPI fetch is warranted.

    Due when either:

    - No version has ever been cached (``latest_seen`` absent) -- e.g.
      right after upgrading to a build that added the field.  Without
      this, an existing user with a recent ``last_checked`` would see a
      blank tooltip status line for up to a full interval.  Bounded:
      one fetch seeds it, then the timestamp rule takes over.
    - ``last_checked`` is missing/unparseable, or >= _CHECK_INTERVAL_DAYS
      old.
    """
    if not state.get("latest_seen"):
        return True  # never cached a version -> seed it now
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


def check_now(current_version: str) -> tuple[str | None, bool]:
    """On-demand version compare against PyPI.  No throttle, no state writes.

    Used by ``/ver.check`` and any other interactive path where the
    user explicitly asked for a fresh comparison -- ``check()``'s
    7-day cadence + silent-failure contract is meant for the
    background banner and would lie to a user typing ``/ver.check``
    on day two.

    Args:
        current_version: Installed termapy version (typically from
            ``importlib.metadata.version("termapy")``).

    Returns:
        Tuple of ``(latest, outdated)``.  ``latest`` is the version
        string PyPI reports, or ``None`` on any fetch error
        (caller decides how loud to be).  ``outdated`` is True iff
        ``latest`` is strictly higher than ``current_version`` per
        PEP 440 ordering; False otherwise (including when
        ``latest`` is None).
    """
    latest = _fetch_pypi_latest()
    if latest is None:
        return None, False
    return latest, _is_newer(latest, current_version)


def cached_status(current_version: str) -> tuple[str | None, bool]:
    """Network-free read of the last PyPI version ``check`` cached.

    For ambient UI (the Help tooltip) that wants to show an
    up-to-date / update-available line without ever blocking on the
    network or risking an error on render.  Reads ``latest_seen`` from
    ``state.json`` -- populated by the background ``check`` -- and
    compares it to ``current_version``.

    The value is at most ``_CHECK_INTERVAL_DAYS`` stale, matching the
    background banner's cadence; interactive callers wanting a fresh
    answer use ``check_now`` instead.

    Args:
        current_version: Installed termapy version.

    Returns:
        Tuple of ``(latest_seen, outdated)``.  ``latest_seen`` is the
        cached PyPI version string, or ``None`` if no check has ever
        succeeded (nothing to show -- caller stays silent).
        ``outdated`` is True iff ``latest_seen`` is strictly higher
        than ``current_version``.  Never raises.
    """
    try:
        latest = _load_state().get("latest_seen")
        if not isinstance(latest, str) or not latest:
            return None, False
        return latest, _is_newer(latest, current_version)
    except Exception:
        # Same silent contract as the rest of the module.
        return None, False


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
        # Record the attempt regardless of success.  On a successful
        # fetch also cache the version PyPI reported (newer or not) so
        # ``cached_status`` can render an up-to-date/behind message
        # network-free -- even on days the 7-day throttle skips the
        # fetch entirely.
        state["last_checked"] = now.isoformat()
        if latest is not None:
            state["latest_seen"] = latest
        _save_state(state)

        if latest is None:
            return None
        if not _is_newer(latest, current_version):
            return None
        return latest
    except Exception:
        # Catch-all guard: nothing from this module should ever propagate.
        return None
