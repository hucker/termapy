"""Check each direct dependency against PyPI's latest release.

Prints a table sorted with "bump available" rows first so you can
see at a glance which deps have moved since you last capped them.
Run manually when you want to decide whether a new release needs
dep bumps.  Not part of release_prep -- release_prep should stay
hermetic (no network calls).

Usage:
    python scripts/check_dep_updates.py

The script:
    1. Reads direct dependencies from pyproject.toml.
    2. Reads currently-locked versions from uv.lock.
    3. Queries PyPI JSON for each dep's latest release.
    4. Compares latest against the dep's upper bound (if any).
    5. Prints a sorted table and exits 0.

Exits with status 1 only on hard failures (pyproject.toml or
uv.lock unreadable).  Network failures for individual deps show
"unreachable" in the LATEST column -- the other rows still render.

Stdlib-only except for ``packaging`` which termapy already depends
on (used for PEP 440 version comparison and specifier parsing).
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

_PYPI_URL = "https://pypi.org/pypi/{name}/json"
_HTTP_TIMEOUT_S = 5.0


@dataclass
class _Row:
    name: str
    locked: str        # version in uv.lock
    latest: str        # latest on PyPI (or "unreachable")
    cap: str           # upper-bound part of the specifier, or "(none)"
    action: str        # "BUMP AVAILABLE", "up to date", "unreachable", etc.
    priority: int      # for sorting -- lower = more urgent


def _load_direct_deps(pyproject: Path) -> list[Requirement]:
    """Parse ``[project].dependencies`` into Requirement objects."""
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    raw = data.get("project", {}).get("dependencies", [])
    return [Requirement(line) for line in raw]


def _load_lock_versions(lockfile: Path) -> dict[str, str]:
    """Build a ``{package_name: version}`` map from uv.lock.

    uv.lock is TOML but its [[package]] sections can repeat (one
    per resolved package); we take the first occurrence.  Names are
    lower-cased for case-insensitive lookup.
    """
    data = tomllib.loads(lockfile.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "").lower()
        version = pkg.get("version", "")
        if name and version and name not in out:
            out[name] = version
    return out


def _fetch_latest(name: str) -> str | None:
    """Return the latest PyPI version of ``name``, or None on any error."""
    req = urllib.request.Request(
        _PYPI_URL.format(name=name),
        headers={"Accept": "application/json", "User-Agent": "termapy-dep-check"},
    )
    try:
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
    ):
        return None


def _format_cap(spec: SpecifierSet) -> str:
    """Extract a human-readable cap from the specifier set.

    Returns e.g. "<0.26" or "(none)".  We only care about upper
    bounds here because those are what expire when upstream
    releases a new version.
    """
    uppers = [s for s in spec if s.operator in ("<", "<=", "==", "~=")]
    return ",".join(str(s) for s in uppers) if uppers else "(none)"


def _decide_action(
    latest: str | None,
    cap: SpecifierSet,
) -> tuple[str, int]:
    """Classify the latest-vs-cap relationship.  Returns (label, priority).

    Priority is used for sorting: 0 = most urgent (bump available),
    higher = less urgent.
    """
    if latest is None:
        return ("unreachable", 5)
    try:
        latest_v = Version(latest)
    except InvalidVersion:
        return ("unparseable", 5)
    if not any(s.operator in ("<", "<=") for s in cap):
        # No upper bound defined -- user takes whatever pip resolves.
        return ("no cap (unbounded)", 4)
    if latest in cap:
        # Fits within the bound -- next `uv lock` will pick it up.
        return ("up to date", 3)
    # Outside the cap: a new release exists that we've chosen not to take.
    return ("BUMP AVAILABLE", 0)


def _build_rows(
    deps: list[Requirement],
    locked: dict[str, str],
) -> list[_Row]:
    rows: list[_Row] = []
    for req in deps:
        name = req.name
        lock_v = locked.get(name.lower(), "?")
        latest = _fetch_latest(name)
        action, priority = _decide_action(latest, req.specifier)
        rows.append(
            _Row(
                name=name,
                locked=lock_v,
                latest=latest or "unreachable",
                cap=_format_cap(req.specifier),
                action=action,
                priority=priority,
            )
        )
    return rows


def _print_table(rows: list[_Row]) -> None:
    if not rows:
        print("(no direct dependencies found)")
        return
    cols = ("DEP", "LOCKED", "LATEST", "CAP", "ACTION")
    rows_sorted = sorted(rows, key=lambda r: (r.priority, r.name.lower()))
    # Column widths sized to the longest cell.
    widths = [
        max(len(cols[0]), max(len(r.name) for r in rows_sorted)),
        max(len(cols[1]), max(len(r.locked) for r in rows_sorted)),
        max(len(cols[2]), max(len(r.latest) for r in rows_sorted)),
        max(len(cols[3]), max(len(r.cap) for r in rows_sorted)),
        max(len(cols[4]), max(len(r.action) for r in rows_sorted)),
    ]
    header = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    print(header)
    print("  ".join("-" * w for w in widths))
    for r in rows_sorted:
        print(
            "  ".join([
                r.name.ljust(widths[0]),
                r.locked.ljust(widths[1]),
                r.latest.ljust(widths[2]),
                r.cap.ljust(widths[3]),
                r.action.ljust(widths[4]),
            ])
        )
    bump_count = sum(1 for r in rows_sorted if r.action == "BUMP AVAILABLE")
    uncapped = sum(1 for r in rows_sorted if r.action == "no cap (unbounded)")
    unreachable = sum(1 for r in rows_sorted if r.action == "unreachable")
    summary: list[str] = []
    if bump_count:
        summary.append(f"{bump_count} bump available")
    if uncapped:
        summary.append(f"{uncapped} uncapped")
    if unreachable:
        summary.append(f"{unreachable} unreachable")
    print()
    print("Summary: " + (", ".join(summary) if summary else "all deps current"))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    lockfile = root / "uv.lock"

    if not pyproject.exists():
        print(f"missing {pyproject}", file=sys.stderr)
        return 1
    if not lockfile.exists():
        print(f"missing {lockfile}", file=sys.stderr)
        return 1

    deps = _load_direct_deps(pyproject)
    locked = _load_lock_versions(lockfile)

    print(f"Checking {len(deps)} direct dependencies against PyPI...")
    print()
    rows = _build_rows(deps, locked)
    _print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
