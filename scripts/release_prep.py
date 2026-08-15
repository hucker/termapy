"""Release prep: cut release branch, bump versions, build, test, commit.

Stops before merging to main. You review the diffs (especially CHANGELOG)
and then run release_publish.py to finish.

Usage:
    python scripts/release_prep.py 0.53.0

What it does:
    1. Sanity-check git state (on main, clean, in sync with origin)
    2. Cut release/v<version> branch from main
    3. Bump version in pyproject.toml and mkdocs.yml, refresh uv.lock
    4. Update doc counts (test count, ty count, line counts)
    5. Update tagged config examples in help docs
    6. Refresh USB vendor table from upstream usb.ids
    7. Insert CHANGELOG stub
    8. Run pytest
    9. Run tox (multi-version)
    10. Build HTML help with zensical
    11. Commit HTML rebuild
    12. Commit release

Aborts loudly on any failure. Safe-restart: if it fails halfway, delete
the release branch and start over (`git checkout main && git branch -D
release/v<version>`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# Allow running as `python scripts/release_prep.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_common import (  # noqa: E402
    REPO_ROOT,
    assert_clean_tree,
    assert_main_in_sync_with_origin,
    assert_on_main,
    assert_tag_does_not_exist,
    assert_tool_available,
    die,
    info,
    last_tag,
    ok,
    run,
    run_out,
    validate_version,
    warn,
)


# ── version bumping ──────────────────────────────────────────────────────────


def bump_pyproject(version: str) -> None:
    path = REPO_ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'^version = "[^"]+"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        die("could not find a single `version = \"...\"` line in pyproject.toml")
    path.write_text(new_text, encoding="utf-8")
    ok(f"pyproject.toml -> {version}")


def bump_mkdocs(version: str) -> None:
    path = REPO_ROOT / "mkdocs.yml"
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r"^site_name: Termapy Help v[\d.]+",
        f"site_name: Termapy Help v{version}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        die("could not find `site_name: Termapy Help v...` in mkdocs.yml")
    path.write_text(new_text, encoding="utf-8")
    ok(f"mkdocs.yml -> v{version}")


def refresh_uv_lock() -> None:
    run(["uv", "lock"])
    ok("uv.lock refreshed")


# ── line counts and test counts ──────────────────────────────────────────────


def count_lines(rel_path: str) -> int:
    """Total lines in a file, or sum across all .py files in a directory.

    Several top-level modules became subpackages this cycle (dialogs/,
    protocol/, plugins/) so callers may pass either a single file or
    a directory path; directories are walked recursively and their
    .py files are summed.  Skips ``__pycache__``.
    """
    path = REPO_ROOT / rel_path
    if not path.exists():
        die(f"file not found for line count: {rel_path}")
    if path.is_dir():
        total = 0
        for f in path.rglob("*.py"):
            if "__pycache__" in str(f):
                continue
            total += len(f.read_text(encoding="utf-8").splitlines())
        return total
    return len(path.read_text(encoding="utf-8").splitlines())


def count_tests() -> int:
    """Total test count from `pytest --collect-only -q`."""
    # --no-cov so the cov plugin's terminal report doesn't bury the summary line.
    out = run_out(["uv", "run", "pytest", "--collect-only", "-q", "--no-cov"])
    # Matches either "1259 tests collected in 0.72s" or the
    # "======= 1259 tests collected in 1.09s =======" decorated form.
    m = re.search(r"(\d+) tests? collected", out)
    if m:
        return int(m.group(1))
    die("could not parse test count from pytest --collect-only output")
    return 0  # unreachable, satisfies type checker


def count_ty_issues() -> int:
    """Total ty diagnostics from `uv run ty check src/termapy/`.

    Vendored code is excluded via [tool.ty.src] in pyproject.toml, so
    the count reflects only first-party files.  A clean project returns
    the literal string "All checks passed!" and we report 0.

    ``ty check`` exits 1 when it finds diagnostics -- which is exactly
    the data we want to count, not crash on -- so we pass
    ``check=False`` and parse the output regardless of exit code.

    Deliberately ``uv run`` and not ``uvx``: uvx would fetch whatever ty
    is newest at release time, and ty is 0.0.x with near-daily releases.
    Since a non-zero count aborts the release, that made the gate's bar
    depend on the calendar.  ty is pinned in [dependency-groups] dev.
    """
    out = run_out(["uv", "run", "ty", "check", "src/termapy/"], check=False)
    if "All checks passed!" in out:
        return 0
    m = re.search(r"Found (\d+) diagnostics?", out)
    if m:
        return int(m.group(1))
    die("could not parse ty diagnostic count from `ty check` output")
    return 0  # unreachable, satisfies type checker


def count_ruff_issues() -> int:
    """Total ruff issues from `uv run ruff check src/termapy/ tests/`.

    ``ruff check`` exits 1 when it finds issues; pass ``check=False``
    and parse the output regardless.  A clean project prints
    ``All checks passed!``.

    ``uv run`` resolves ruff from the project venv, where it is pinned in
    [dependency-groups] dev.  Before that pin it fell through to whatever
    ruff sat on PATH, so the gate enforced whichever rule set that build
    happened to default to -- see the note on the pin for how far that
    drifted.
    """
    out = run_out(
        ["uv", "run", "ruff", "check", "src/termapy/", "tests/"],
        check=False,
    )
    if "All checks passed!" in out:
        return 0
    m = re.search(r"Found (\d+) errors?", out)
    if m:
        return int(m.group(1))
    die("could not parse ruff issue count from `ruff check` output")
    return 0  # unreachable, satisfies type checker


def measure_coverage_percent() -> int:
    """Total coverage percent from ``pytest --cov``.

    Runs a fresh quick pytest with the default ``-v --cov=termapy
    --cov-report=term-missing`` addopts (from pyproject), parses the
    ``TOTAL ... N%`` line at the end of the cov summary, and returns
    the integer percent.  The release commit substitutes this number
    into README.md so the figure can't silently drift.
    """
    out = run_out(["uv", "run", "pytest", "-q"])
    # The coverage terminal report ends with:
    #     TOTAL                       9562    1834    81%
    m = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", out, re.MULTILINE)
    if m:
        return int(m.group(1))
    die("could not parse coverage percent from pytest --cov output")
    return 0  # unreachable, satisfies type checker


def assert_zero_lint() -> None:
    """Hard-fail the release if ruff or ty has any issues.

    The release process treats both at zero as a precondition, not a
    "nice to have."  Surfaces the count and aborts so the user fixes
    the source on a chore branch and re-runs release_prep -- rather
    than baking the regression into a published version.
    """
    ruff = count_ruff_issues()
    ty = count_ty_issues()
    if ruff or ty:
        die(
            f"refusing to release with lint issues: "
            f"ruff={ruff}, ty={ty}.  Fix on a chore branch first."
        )
    ok("lint clean (ruff=0, ty=0)")


def assert_no_bad_suppressions() -> None:
    """Hard-fail if a suppression added since the last release is undisciplined.

    A suppression (``# noqa`` / ``# ty: ignore`` / ``# type: ignore`` /
    ``# pragma: no cover``) is sometimes the right call, but it must never be
    the *easy* one.  Every suppression introduced since the previous tag must
    name a specific rule code AND carry a reason -- inline, or on the comment
    line directly above it; otherwise it's an unexamined override and the
    release aborts.  Pre-existing suppressions in untouched code are not
    policed here -- only what the diff introduces.
    """
    import suppression_audit

    tag = suppression_audit.previous_tag(REPO_ROOT)
    if tag is None:
        ok("suppression gate skipped (no prior tag)")
        return
    offenders = suppression_audit.gate_offenders(
        suppression_audit.added_since(tag, REPO_ROOT), REPO_ROOT
    )
    if offenders:
        listing = "\n".join(f"    {s.describe()}" for s in offenders)
        die(
            f"refusing to release: {len(offenders)} suppression(s) added since "
            f"{tag} lack a rule code and/or a reason:\n{listing}\n"
            "  Every suppression must name its rule and say why "
            "(e.g. `# noqa: BLE001 -- boundary thread`)."
        )
    ok(f"suppression gate clean (no bare/reason-less additions since {tag})")


def update_suppression_count() -> None:
    """Refresh the ARCHITECTURE.md suppression count and print the inventory.

    The full per-line inventory (location, form, remediation status) is
    printed to the release log so the operator can eyeball every override
    at release time -- not just the total.
    """
    import suppression_audit

    supps = suppression_audit.scan_tree(REPO_ROOT)
    total = len(supps)
    path = REPO_ROOT / "ARCHITECTURE.md"
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r"\d+( lint/type/coverage pragmas in)",
        rf"{total}\g<1>",
        text, count=1,
    )
    if n != 1:
        die("ARCHITECTURE.md is missing the Suppressions count line to refresh.")
    path.write_text(new_text, encoding="utf-8")
    ok(f"ARCHITECTURE.md suppression count updated ({total})")
    info(f"Suppression inventory ({total}):")
    for line in suppression_audit.format_list(supps, REPO_ROOT):
        print(line)


def ty_badge_color(count: int) -> str:
    """Return the shields.io color name for a given ty issue count.

    Thresholds match the README badge scheme: 0-9 green, 10-19 yellow,
    20+ red.
    """
    if count < 10:
        return "brightgreen"
    if count < 20:
        return "yellow"
    return "red"


def coverage_badge_color(percent: int) -> str:
    """Return the shields.io color name for a coverage percentage.

    Thresholds chosen to give visible "you're slipping" feedback as
    coverage drops below the project's typical 70-80% band: 80+ green,
    65-79 yellow, below 65 red.
    """
    if percent >= 80:
        return "brightgreen"
    if percent >= 65:
        return "yellow"
    return "red"


def update_architecture_md() -> None:
    """Update per-module line counts in ARCHITECTURE.md.

    The test count is NOT tracked here on purpose -- it lives in exactly one
    place (README's Test coverage summary).  ARCHITECTURE.md and other prose
    say "extensively tested" so there is nothing to keep in sync.
    """
    path = REPO_ROOT / "ARCHITECTURE.md"
    text = path.read_text(encoding="utf-8")

    # Files / packages whose line counts ARCHITECTURE.md tracks.  Keep
    # in sync with the tree diagram block.  Directory entries are
    # walked recursively by count_lines() and rendered with a trailing
    # slash in ARCHITECTURE.md (e.g. ``├── dialogs/  # (1965 lines)``).
    tracked = [
        "src/termapy/app.py",
        "src/termapy/cli.py",
        "src/termapy/serial_engine.py",
        "src/termapy/serial_port.py",
        "src/termapy/capture.py",
        "src/termapy/dialogs",      # package (was dialogs.py)
        "src/termapy/proto_debug.py",
        "src/termapy/protocol",     # package (was protocol.py + friends)
        "src/termapy/demo.py",
        "src/termapy/repl.py",
        "src/termapy/plugins",      # package (was plugins.py)
        "src/termapy/config.py",
        "src/termapy/port_control.py",
        "src/termapy/scripting.py",
        "src/termapy/migration.py",
        "src/termapy/defaults.py",
        "src/termapy/mcp",          # package
        "src/termapy/profile",      # package
        "src/termapy/usb",          # package
    ]
    missing: list[str] = []
    for rel in tracked:
        basename = Path(rel).name
        actual = count_lines(rel)
        # Match either a file (``├── app.py    # (N lines)``) or a
        # directory entry (``├── dialogs/  # (N lines)``).  The
        # ``/?`` after the basename absorbs the trailing slash on
        # package rows.
        pattern = re.compile(
            rf"(├── {re.escape(basename)}/?\s+# \()\d+( lines\))"
        )
        new_text, n = pattern.subn(rf"\g<1>{actual}\g<2>", text, count=1)
        if n == 1:
            text = new_text
        else:
            missing.append(rel)

    # Fail loud, never drift: every tracked entry must have a matching
    # ``(N lines)`` row in the tree diagram.  A silent miss would leave a
    # stale exact count in the doc -- the exact failure mode the
    # numbers-only-in-auto-updated-spots policy exists to prevent.
    if missing:
        die(
            "ARCHITECTURE.md tree rows missing for tracked entries "
            f"(reword the tree or the `tracked` list): {', '.join(missing)}"
        )

    path.write_text(text, encoding="utf-8")
    ok(f"ARCHITECTURE.md updated ({len(tracked)} line counts)")


def update_readme_md(test_count: int, ty_count: int, cov_percent: int) -> None:
    """Update the Test coverage summary line and the ty + coverage badges.

    These are the ONLY exact figures README carries -- the summary line
    (test count + coverage %) and the two badges.  Everything else is
    general-terms prose with no numbers to keep in sync; per-module line
    counts live in ARCHITECTURE.md's tree (see update_architecture_md).
    """
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")

    # The test count lives in exactly ONE place: the <details> Test
    # coverage summary line.  The body line below it is count-free prose,
    # so only the summary's count + the coverage % are refreshed here.
    # ``cov_percent`` is the coverage config's reported number, which omits
    # app.py/dialogs/builtins (see pyproject [tool.coverage.run]) -- i.e.
    # core-module coverage, NOT whole-repo.  The wording says "core-module"
    # rather than "overall" on purpose; don't revert it to "overall".
    text, n = re.subn(
        r"<strong>Test coverage</strong> - \d+ tests, \d+% core-module coverage",
        f"<strong>Test coverage</strong> - {test_count} tests, {cov_percent}% core-module coverage",
        text,
        count=1,
    )
    if n != 1:
        die("could not find the Test coverage <summary> line in README.md")

    # ty badge: count + color track how many diagnostics ty currently
    # reports.  Thresholds: 0-9 green, 10-19 yellow, 20+ red.
    color = ty_badge_color(ty_count)
    text, n = re.subn(
        r"badge/ty-\d+%20issues-[a-z]+",
        f"badge/ty-{ty_count}%20issues-{color}",
        text,
        count=1,
    )
    if n != 1:
        die("could not find the ty badge in README.md")

    # Coverage badge in the "Built with:" row.  Drives the displayed
    # percent from pytest --cov output; coverage_badge_color() picks
    # green/yellow/red so a drop is visible at a glance.
    cov_color = coverage_badge_color(cov_percent)
    text, n = re.subn(
        r"badge/coverage-[^-]+-[a-z]+",
        f"badge/coverage-{cov_percent}%25-{cov_color}",
        text,
        count=1,
    )
    if n != 1:
        die("could not find the coverage badge in README.md")

    path.write_text(text, encoding="utf-8")
    ok(
        f"README.md updated (tests={test_count}, ty={ty_count} ({color}), "
        f"coverage={cov_percent}% ({cov_color}))"
    )


# ── changelog ────────────────────────────────────────────────────────────────


def refresh_usb_vendor_table() -> None:
    """Pull latest upstream usb.ids and regenerate usb/_vendors_full.py.

    Idempotent -- if upstream hasn't changed since the last release,
    the regeneration produces byte-identical output and no diff lands
    in the release commit.  When upstream has changed, the new vendor
    entries are picked up automatically and become part of the release.

    Defensive checks (release aborts if any fail):

      - **Monotonic growth.**  The upstream usb.ids table essentially
        never shrinks meaningfully; new VIDs get assigned, retired
        vendors keep their entries for ``lsusb`` backward-compat, and
        merges typically add notes rather than remove lines.  We
        compare the freshly-parsed count against the count baked into
        the previous ``usb/_vendors_full.py`` and require the new value
        to be within a small tolerance below the old one (typo /
        dedup pass = OK; sudden 100-entry drop = format change or
        parser bug, abort).  First-time generation gets a floor check
        of 1000 instead.

      - **Spot-check stable VIDs.**  Three decades-old assignments
        (FTDI 0x0403, Silicon Labs 0x10C4, Microchip 0x04D8) must
        still resolve to recognizable names.

      - **Compilable Python.**  The generated module must parse and
        import cleanly.
    """
    # Was ``src/termapy/_usb_vendor_full.py`` before the v0.65 ``usb/``
    # subpackage refactor; the generator (scripts/refresh_usb_ids.py)
    # writes to the new location below.
    full_path = REPO_ROOT / "src" / "termapy" / "usb" / "_vendors_full.py"
    # Capture the previous count from the generated module's header
    # (``Entries:   3427``).  Falls back to None if the file doesn't
    # exist yet or the header line is absent.
    previous_count = _read_previous_vendor_count(full_path)

    run([sys.executable, "scripts/refresh_usb_ids.py"])
    # Sanity-check the freshly-generated file before letting the release
    # proceed.  Importing the module also serves as the syntax check.
    ns: dict = {}
    exec(compile(full_path.read_text(encoding="utf-8"), str(full_path), "exec"), ns)
    table = ns.get("USB_VENDORS_FULL")
    if not isinstance(table, dict):
        die("usb/_vendors_full.py did not define USB_VENDORS_FULL as a dict")

    new_count = len(table)
    # Allow a tiny shrinkage (typo-fix / dedup pass).  Anything beyond
    # this is suspicious enough to halt the release for review.  Three
    # entries was picked empirically: real upstream cleanups historically
    # touch 1-2 lines; a 5-entry drop has no benign explanation.
    SHRINK_TOLERANCE = 3
    if previous_count is None:
        # Bootstrap: no prior baseline; use a coarse floor.
        if new_count < 1000:
            die(
                f"USB_VENDORS_FULL has only {new_count} entries with no "
                f"baseline -- upstream may have changed format"
            )
    else:
        if new_count < previous_count - SHRINK_TOLERANCE:
            die(
                f"USB_VENDORS_FULL shrank from {previous_count} to "
                f"{new_count} entries (tolerance {SHRINK_TOLERANCE}). "
                "Upstream may have changed format, or the parser is "
                "missing entries.  Investigate before re-running."
            )

    # Spot-check a few stable, well-known assignments.  If any of these
    # vanish, something is wrong with either upstream or the parser.
    spot_checks = [
        (0x0403, "Future Technology"),  # FTDI
        (0x10C4, "Silicon Lab"),        # Silicon Labs
        (0x04D8, "Microchip"),          # Microchip Technology
    ]
    for vid, expected_substring in spot_checks:
        actual = table.get(vid, "")
        if expected_substring.lower() not in actual.lower():
            die(
                f"VID 0x{vid:04X} expected to contain {expected_substring!r}; "
                f"got {actual!r}"
            )

    delta = (
        f"{new_count - previous_count:+d}"
        if previous_count is not None else "first run"
    )
    ok(f"USB vendor table refreshed ({new_count} entries, {delta})")


def _read_previous_vendor_count(path: Path) -> int | None:
    """Extract the ``Entries:`` value from the generated module's header.

    Returns ``None`` if the file doesn't exist (first-time generation)
    or the header line is missing / unparseable.  The count line is
    written by ``scripts/refresh_usb_ids.py`` and looks like::

        Entries:   3427
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^Entries:\s+(\d+)", text, flags=re.MULTILINE)
    if not m:
        return None
    return int(m.group(1))


def insert_changelog_stub(version: str) -> None:
    """Insert a CHANGELOG stub for the new version, populated with git log."""
    path = REPO_ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")

    # Get commits since last tag
    try:
        prev = last_tag()
    except Exception:
        prev = None

    if prev:
        log_range = f"{prev}..HEAD"
        commits = run_out(
            ["git", "log", log_range, "--oneline", "--no-merges"]
        )
    else:
        commits = run_out(["git", "log", "--oneline", "--no-merges"])

    today = dt.date.today().isoformat()
    bullet_lines = []
    for line in commits.splitlines():
        line = line.strip()
        if not line:
            continue
        # Drop the SHA prefix; keep the subject
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            bullet_lines.append(f"- {parts[1]}")

    bullets = "\n".join(bullet_lines) if bullet_lines else "- (no commits found)"

    stub = (
        f"## {version} ({today})\n"
        f"\n"
        f"### {version} New Features\n"
        f"\n"
        f"<!-- TODO: write the user-facing summary. Commits since {prev or 'beginning'}: -->\n"
        f"{bullets}\n"
        f"\n"
        f"### {version} Improvements\n"
        f"\n"
        f"<!-- TODO: write the user-facing summary. -->\n"
        f"\n"
    )

    # Insert after the first line ("# Changelog").  Use a lambda
    # replacement so backslashes in the stub (e.g. "Enum\\USB" from a
    # Windows-registry-path commit message) aren't interpreted as
    # regex back-references / Unicode escapes.
    new_text = re.sub(
        r"(# Changelog\n+)",
        lambda m: m.group(0) + stub,
        text,
        count=1,
    )
    if new_text == text:
        die("could not find '# Changelog' header in CHANGELOG.md")
    path.write_text(new_text, encoding="utf-8")
    ok(f"CHANGELOG.md stub inserted for {version} (edit before publishing)")


# ── test runs ────────────────────────────────────────────────────────────────


def run_pytest() -> None:
    info("Running pytest...")
    run(["uv", "run", "pytest", "-q"])
    ok("pytest passed")


def run_tox() -> None:
    info("Running tox (multi-version)...")
    run(["tox"])
    ok("tox passed")


def run_zensical_build() -> None:
    info("Building HTML help with zensical...")
    run(["uvx", "zensical", "build"])
    ok("HTML help built")
    _assert_html_is_fresh()


def _assert_html_is_fresh() -> None:
    """Verify every help/*.md has a matching html/*.html at least as new.

    Catches the case where zensical exits 0 but fails to regenerate
    one or more pages -- without this check, that would silently ship
    a stale HTML doc in the wheel.  Run immediately after
    ``run_zensical_build`` so any failure aborts before we commit the
    rebuild.
    """
    help_dir = REPO_ROOT / "src" / "termapy" / "help"
    html_dir = REPO_ROOT / "src" / "termapy" / "html"

    md_files = sorted(help_dir.glob("*.md"))
    if not md_files:
        die(f"no markdown sources found in {help_dir}")

    missing: list[str] = []
    stale: list[str] = []
    for md in md_files:
        html = html_dir / f"{md.stem}.html"
        if not html.exists():
            missing.append(md.name)
            continue
        if html.stat().st_mtime < md.stat().st_mtime:
            stale.append(md.name)

    if missing or stale:
        parts: list[str] = []
        if missing:
            parts.append(f"no .html counterpart: {', '.join(missing)}")
        if stale:
            parts.append(f".html older than .md: {', '.join(stale)}")
        die("HTML help is out of sync after zensical build -- " + "; ".join(parts))
    ok(f"HTML help freshness verified ({len(md_files)} pages)")


# ── git operations ───────────────────────────────────────────────────────────


def cut_release_branch(version: str) -> None:
    branch = f"release/v{version}"
    # Check if branch already exists locally
    existing = run_out(["git", "branch", "--list", branch])
    if existing:
        die(
            f"branch {branch!r} already exists locally. "
            f"To start over: `git checkout main && git branch -D {branch}`"
        )
    run(["git", "checkout", "-b", branch])
    ok(f"on branch {branch}")


def commit_html_rebuild(version: str) -> None:
    """Stage src/termapy/html/ and commit as the HTML rebuild commit."""
    # Stage only the html directory
    run(["git", "add", "src/termapy/html/"])
    # Anything to commit?
    status = run_out(["git", "status", "--porcelain", "src/termapy/html/"])
    if not status:
        warn("no HTML changes to commit (zensical produced no diff)")
        return
    msg = (
        f"docs(html): rebuild zensical HTML help for v{version}\n"
        f"\n"
        f"Regenerated with uvx zensical build."
    )
    run(["git", "commit", "-m", msg])
    ok("HTML rebuild committed")


def commit_release(version: str) -> None:
    """Stage version-bump files and commit as the release commit."""
    files = [
        "pyproject.toml",
        "mkdocs.yml",
        "uv.lock",
        "CHANGELOG.md",
        "README.md",
        "ARCHITECTURE.md",
    ]
    run(["git", "add", *files])
    status = run_out(["git", "status", "--porcelain", *files])
    if not status:
        die("no release-bump changes to commit. Did the version bump steps run?")
    msg = f"Release v{version}"
    run(["git", "commit", "-m", msg])
    ok(f"release commit created (v{version})")


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="New version, e.g. 0.53.0 (no leading 'v')")
    args = parser.parse_args()

    version = args.version
    validate_version(version)

    info(f"Preparing release v{version}")

    total = 12

    def step(n: int, label: str) -> None:
        info(f"[{n}/{total}] {label}")

    step(1, "Checking environment and git state...")
    assert_tool_available("git")
    assert_tool_available("uv")
    assert_tool_available("tox")
    assert_tool_available("uvx")
    assert_on_main()
    assert_clean_tree()
    assert_main_in_sync_with_origin()
    assert_tag_does_not_exist(version)
    assert_zero_lint()
    assert_no_bad_suppressions()
    ok("git state is clean and ready")

    step(2, "Cutting release branch...")
    cut_release_branch(version)

    step(3, "Bumping version files...")
    bump_pyproject(version)
    bump_mkdocs(version)
    refresh_uv_lock()

    step(4, "Updating doc counts (test count, ty count, coverage, line counts)...")
    test_count = count_tests()
    ty_count = count_ty_issues()
    cov_percent = measure_coverage_percent()
    update_architecture_md()
    update_readme_md(test_count, ty_count, cov_percent)
    update_suppression_count()

    step(5, "Refreshing generated docs (config examples, credits sync)...")
    from update_doc_configs import update_doc_configs
    updated = update_doc_configs()
    if updated:
        ok(f"updated {len(updated)} config example(s)")
    else:
        ok("all config examples current")
    # credits.py embeds help/acknowledgments.md as a string constant so
    # the wheel can ship zero markdown.  The sync script regenerates the
    # block between BEGIN/END sentinels; tests/test_credits_sync.py
    # asserts the result matches the .md.  Running --check here gives
    # the release operator a clean error before pytest does the same.
    from sync_acknowledgments import main as sync_credits
    if sync_credits(["--check"]) != 0:
        die(
            "credits.py drifted from help/acknowledgments.md.  "
            "Run scripts/sync_acknowledgments.py on a chore branch first."
        )
    ok("credits.py in sync with help/acknowledgments.md")

    step(6, "Refreshing USB vendor table from upstream usb.ids...")
    refresh_usb_vendor_table()

    step(7, "Inserting CHANGELOG stub...")
    insert_changelog_stub(version)

    step(8, "Running pytest...")
    run_pytest()

    step(9, "Running tox (multi-version)...")
    run_tox()

    step(10, "Building HTML help with zensical...")
    run_zensical_build()

    step(11, "Committing HTML rebuild...")
    commit_html_rebuild(version)

    step(12, "Committing release...")
    commit_release(version)

    # ── Done ─────────────────────────────────────────────────────────────
    print()
    ok(f"Release v{version} prepped on branch release/v{version}")
    print()
    info("Next steps:")
    print("  1. Review the diff:        git log -p main..HEAD")
    print("  2. Edit CHANGELOG.md       (the stub has TODO markers)")
    print("  3. Amend if you edit it:   git add CHANGELOG.md && git commit --amend --no-edit")
    print("  4. Publish:                python scripts/release_publish.py --yes")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("aborted by user", code=130)
