"""Tests for update_check.check and its bail-out paths.

The core invariant: any failure path returns None silently.  No
exception, no stdout noise, no user-visible artifact other than the
potentially-updated state file.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from termapy import update_check

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def pin_state_dir(monkeypatch, tmp_path):
    """Redirect app_state_dir to tmp_path for the duration of the test."""
    monkeypatch.setenv("TERMAPY_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def freeze_time(monkeypatch):
    """Freeze update_check._now() to a settable datetime."""
    holder = {"now": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(update_check, "_now", lambda: holder["now"])
    return holder


@pytest.fixture
def mock_pypi(monkeypatch):
    """Pin update_check._fetch_pypi_latest to a settable return value."""
    holder = {"result": None}
    monkeypatch.setattr(
        update_check,
        "_fetch_pypi_latest",
        lambda: holder["result"],
    )
    return holder


@pytest.fixture(autouse=True)
def pin_check_interval(monkeypatch):
    """Tests assume the 7-day check interval regardless of any dev
    override of the constant.  Applied automatically."""
    monkeypatch.setattr(update_check, "_CHECK_INTERVAL_DAYS", 7)


def _write_state(tmp_path: Path, state: dict) -> None:
    """Seed state.json with only the update_check slice populated."""
    (tmp_path / "state.json").write_text(
        json.dumps({"update_check": state})
    )


def _read_state(tmp_path: Path) -> dict:
    """Return the update_check slice of state.json (empty if missing)."""
    path = tmp_path / "state.json"
    if not path.exists():
        return {}
    whole = json.loads(path.read_text())
    return whole.get("update_check") or {}


# -- Happy path --------------------------------------------------------------


class TestHappyPath:
    def test_first_check_new_version_returns_version(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - no prior state, PyPI reports a newer release.
        mock_pypi["result"] = "0.61.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        expected = "0.61.0"
        assert actual == expected, "new version returned for the banner"
        state = _read_state(pin_state_dir)
        assert "last_checked" in state, "last-check timestamp saved"


# -- 7-day check interval ----------------------------------------------------


class TestCheckInterval:
    def test_recent_check_skips_fetch(
        self, pin_state_dir, freeze_time, mock_pypi, monkeypatch
    ):
        # Arrange - last check was 1 day ago AND a version is already
        # cached, so the throttle applies; fetch must not be called.
        now = freeze_time["now"]
        _write_state(
            pin_state_dir,
            {
                "last_checked": (now - timedelta(days=1)).isoformat(),
                "latest_seen": "0.60.0",
            },
        )
        fetch_called = [False]

        def _fail_if_fetched():
            fetch_called[0] = True
            return "0.61.0"

        monkeypatch.setattr(update_check, "_fetch_pypi_latest", _fail_if_fetched)

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual is None, "returns None when inside 7-day window"
        assert fetch_called[0] is False, "skips network when inside window"

    def test_missing_latest_seen_forces_check_despite_recent_timestamp(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - recent last_checked but NO cached version (the state
        # of an existing user right after upgrading to this build).  The
        # tooltip needs a value, so we must fetch once to seed it even
        # though the 7-day window hasn't elapsed.
        now = freeze_time["now"]
        _write_state(
            pin_state_dir,
            {"last_checked": (now - timedelta(days=1)).isoformat()},
        )
        mock_pypi["result"] = "0.66.0"

        # Act
        update_check.check(current_version="0.66.0")

        # Assert
        state = _read_state(pin_state_dir)
        assert state.get("latest_seen") == "0.66.0", (
            "missing latest_seen forces a one-time seeding fetch"
        )

    def test_old_check_triggers_fetch(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - last check was 10 days ago; due for new check.
        now = freeze_time["now"]
        _write_state(
            pin_state_dir,
            {"last_checked": (now - timedelta(days=10)).isoformat()},
        )
        mock_pypi["result"] = "0.61.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual == "0.61.0", "fetches and returns after 7-day window"

    def test_exactly_7_days_is_due(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - last check exactly 7 days ago is due (>=).
        now = freeze_time["now"]
        _write_state(
            pin_state_dir,
            {"last_checked": (now - timedelta(days=7)).isoformat()},
        )
        mock_pypi["result"] = "0.61.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual == "0.61.0", "7 days exact is eligible"


# -- Version comparison ------------------------------------------------------


class TestVersionCompare:
    def test_same_version_returns_none(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - PyPI reports the same version we're running.
        mock_pypi["result"] = "0.60.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual is None, "no banner when versions match"

    def test_older_version_returns_none(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - user is ahead of PyPI (dev install).
        mock_pypi["result"] = "0.59.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual is None, "no banner when PyPI is older"

    def test_invalid_version_string_returns_none(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - PyPI returns garbage in the version field.
        mock_pypi["result"] = "not-a-version"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual is None, "unparseable version -> silent None"


# -- Fetch failure -----------------------------------------------------------


class TestFetchFailure:
    def test_pypi_unreachable_returns_none(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - _fetch_pypi_latest returns None on any network issue.
        mock_pypi["result"] = None

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual is None, "network failure is silent"
        state = _read_state(pin_state_dir)
        assert "last_checked" in state, \
            "records attempt timestamp so we don't hammer on flaky net"


# -- State file corruption ---------------------------------------------------


class TestStateCorruption:
    def test_malformed_json_treated_as_empty(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange - write junk bytes where JSON should be.
        (pin_state_dir / "state.json").write_text("{not valid json")
        mock_pypi["result"] = "0.61.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert - corrupt state treated as "never checked", so we notify.
        assert actual == "0.61.0", "corrupt state is tolerated, not fatal"

    def test_missing_state_dir_still_works(
        self, monkeypatch, tmp_path, freeze_time, mock_pypi
    ):
        # Arrange - point to a dir that doesn't exist yet.
        monkeypatch.setenv("TERMAPY_STATE_DIR", str(tmp_path / "does_not_exist"))
        mock_pypi["result"] = "0.61.0"

        # Act
        actual = update_check.check(current_version="0.60.0")

        # Assert
        assert actual == "0.61.0", "missing dir auto-created"


# -- The 'never raises' guarantee --------------------------------------------


class TestNeverRaises:
    def test_arbitrary_exception_in_fetch_is_swallowed(
        self, pin_state_dir, freeze_time, monkeypatch
    ):
        # Arrange - make _fetch_pypi_latest raise something unexpected.
        def _explode():
            raise RuntimeError("totally unexpected")

        monkeypatch.setattr(update_check, "_fetch_pypi_latest", _explode)

        # Act / Assert - must not raise
        actual = update_check.check(current_version="0.60.0")
        assert actual is None, "unexpected exception swallowed, returns None"


# -- check_now: on-demand path used by /ver.latest and /ver.info ------------


class TestCheckNow:
    """``check_now`` powers ``/ver.latest`` and ``/ver.info`` -- the
    user-typed-the-command path.  Different contract from
    ``check``: no 7-day throttle, no state-file writes, network
    failure returns ``(None, False)`` for the caller to surface.
    """

    def test_returns_latest_and_outdated_true_when_newer(self, mock_pypi):
        # Arrange
        mock_pypi["result"] = "0.66.1"

        # Act
        latest, outdated = update_check.check_now("0.66.0")

        # Assert
        assert latest == "0.66.1", "PyPI version returned verbatim"
        assert outdated is True, "0.66.1 > 0.66.0 -> outdated"

    def test_returns_latest_and_outdated_false_when_same(self, mock_pypi):
        # Arrange
        mock_pypi["result"] = "0.66.0"

        # Act
        latest, outdated = update_check.check_now("0.66.0")

        # Assert
        assert latest == "0.66.0", "PyPI version returned even when same"
        assert outdated is False, "same version -> not outdated"

    def test_returns_none_when_pypi_fetch_fails(self, mock_pypi):
        # Arrange
        mock_pypi["result"] = None

        # Act
        latest, outdated = update_check.check_now("0.66.0")

        # Assert
        assert latest is None, "fetch failure surfaces as None"
        assert outdated is False, "no latest -> can't be outdated"

    def test_does_not_touch_state_file(
        self, pin_state_dir, mock_pypi,
    ):
        # Arrange -- check_now must NOT write state.json (that's the
        # background banner's contract; this is the interactive path).
        mock_pypi["result"] = "0.66.1"

        # Act
        update_check.check_now("0.66.0")

        # Assert
        assert not (pin_state_dir / "state.json").exists(), (
            "check_now must not write state.json -- only check() does"
        )

    def test_bypasses_7_day_throttle(
        self, pin_state_dir, freeze_time, mock_pypi,
    ):
        # Arrange -- state.json shows we just checked 1 minute ago.
        # check() would skip; check_now must fetch anyway.
        now = freeze_time["now"]
        _write_state(pin_state_dir, {
            "last_checked": (now - timedelta(minutes=1)).isoformat(),
        })
        mock_pypi["result"] = "0.66.1"

        # Act
        latest, outdated = update_check.check_now("0.66.0")

        # Assert -- throttle is bypassed; we got the PyPI value.
        assert latest == "0.66.1", "throttle bypassed; PyPI hit anyway"
        assert outdated is True, "still detects newer version"


# -- latest_seen persistence (feeds the Help-tooltip status line) ------------


class TestLatestSeenPersistence:
    """``check`` caches the PyPI version it saw so ``cached_status`` can
    render an up-to-date / behind line network-free -- even on days the
    7-day throttle skips the fetch.
    """

    def test_caches_latest_seen_on_successful_fetch(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange -- first check, PyPI reports a newer release.
        mock_pypi["result"] = "0.61.0"

        # Act
        update_check.check(current_version="0.60.0")

        # Assert
        state = _read_state(pin_state_dir)
        assert state.get("latest_seen") == "0.61.0", (
            "PyPI version cached for the tooltip status line"
        )

    def test_caches_latest_seen_even_when_up_to_date(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange -- installed == latest; check() returns None (no banner)
        # but must still cache the version so the tooltip can say so.
        mock_pypi["result"] = "0.66.0"

        # Act
        result = update_check.check(current_version="0.66.0")

        # Assert
        assert result is None, "no banner when already current"
        state = _read_state(pin_state_dir)
        assert state.get("latest_seen") == "0.66.0", (
            "latest cached even when not newer -- powers the up-to-date line"
        )

    def test_failed_fetch_preserves_prior_latest_seen(
        self, pin_state_dir, freeze_time, mock_pypi
    ):
        # Arrange -- a prior good check cached 0.66.0; now (>7 days later)
        # the network is down.  The last known version must survive.
        now = freeze_time["now"]
        _write_state(pin_state_dir, {
            "last_checked": (now - timedelta(days=10)).isoformat(),
            "latest_seen": "0.66.0",
        })
        mock_pypi["result"] = None  # fetch fails this time

        # Act
        update_check.check(current_version="0.65.0")

        # Assert
        state = _read_state(pin_state_dir)
        assert state.get("latest_seen") == "0.66.0", (
            "network failure preserves the last known PyPI version"
        )


# -- cached_status (network-free tooltip read) -------------------------------


class TestCachedStatus:
    """Network-free status read for ambient UI (the Help tooltip)."""

    def test_none_when_never_checked(self, pin_state_dir, monkeypatch):
        # Arrange -- no state file, and the network must never be touched.
        def _no_network():
            raise AssertionError("cached_status must not hit the network")

        monkeypatch.setattr(update_check, "_fetch_pypi_latest", _no_network)

        # Act
        latest, outdated = update_check.cached_status("0.66.0")

        # Assert
        assert latest is None, "no cached version -> nothing to show"
        assert outdated is False, "no latest -> can't be outdated"

    def test_outdated_true_when_cached_is_newer(self, pin_state_dir):
        # Arrange
        _write_state(pin_state_dir, {"latest_seen": "0.67.0"})

        # Act
        latest, outdated = update_check.cached_status("0.66.0")

        # Assert
        assert latest == "0.67.0", "cached version surfaced verbatim"
        assert outdated is True, "0.67.0 > 0.66.0 -> update available"

    def test_outdated_false_when_cached_equals_current(self, pin_state_dir):
        # Arrange
        _write_state(pin_state_dir, {"latest_seen": "0.66.0"})

        # Act
        latest, outdated = update_check.cached_status("0.66.0")

        # Assert
        assert latest == "0.66.0", "cached version surfaced verbatim"
        assert outdated is False, "same version -> up to date"

    def test_non_string_latest_seen_is_ignored(self, pin_state_dir):
        # Arrange -- corrupt state must not raise, just yield nothing.
        _write_state(pin_state_dir, {"latest_seen": 123})

        # Act
        latest, outdated = update_check.cached_status("0.66.0")

        # Assert
        assert latest is None, "non-string cached value treated as absent"
        assert outdated is False, "nothing to compare -> not outdated"
