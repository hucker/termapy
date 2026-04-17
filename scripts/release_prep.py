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
    6. Insert CHANGELOG stub
    7. Run pytest
    8. Run tox (multi-version)
    9. Build HTML help with zensical
    10. Commit HTML rebuild
    11. Commit release

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
    path = REPO_ROOT / rel_path
    if not path.exists():
        die(f"file not found for line count: {rel_path}")
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


def count_test_files() -> int:
    """Number of test_*.py files in tests/."""
    return len(list((REPO_ROOT / "tests").glob("test_*.py")))


def count_ty_issues() -> int:
    """Total ty diagnostics from `uvx ty check src/termapy/`.

    Vendored code is excluded via [tool.ty.src] in pyproject.toml, so
    the count reflects only first-party files.  A clean project returns
    the literal string "All checks passed!" and we report 0.
    """
    out = run_out(["uvx", "ty", "check", "src/termapy/"])
    if "All checks passed!" in out:
        return 0
    m = re.search(r"Found (\d+) diagnostics?", out)
    if m:
        return int(m.group(1))
    die("could not parse ty diagnostic count from `ty check` output")
    return 0  # unreachable, satisfies type checker


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


def update_architecture_md(test_count: int) -> None:
    """Update line counts and test count in ARCHITECTURE.md."""
    path = REPO_ROOT / "ARCHITECTURE.md"
    text = path.read_text(encoding="utf-8")

    # Files whose line counts ARCHITECTURE.md tracks. We try to keep this in
    # sync with the tree diagram block.
    tracked = [
        "src/termapy/app.py",
        "src/termapy/cli.py",
        "src/termapy/serial_engine.py",
        "src/termapy/serial_port.py",
        "src/termapy/capture.py",
        "src/termapy/dialogs.py",
        "src/termapy/proto_debug.py",
        "src/termapy/protocol.py",
        "src/termapy/demo.py",
        "src/termapy/repl.py",
        "src/termapy/plugins.py",
        "src/termapy/config.py",
        "src/termapy/port_control.py",
        "src/termapy/proto_runner.py",
        "src/termapy/scripting.py",
        "src/termapy/migration.py",
        "src/termapy/defaults.py",
    ]
    updated = 0
    for rel in tracked:
        basename = Path(rel).name
        actual = count_lines(rel)
        # Match `├── basename ... # (NNN lines) ...`
        pattern = re.compile(
            rf"(├── {re.escape(basename)}\s+# \()\d+( lines\))"
        )
        new_text, n = pattern.subn(rf"\g<1>{actual}\g<2>", text, count=1)
        if n == 1:
            text = new_text
            updated += 1

    # Update test count line: "28 test files, 1259 tests, 67% overall coverage:"
    test_files = count_test_files()
    text = re.sub(
        r"(\d+) test files, \d+ tests,",
        f"{test_files} test files, {test_count} tests,",
        text,
        count=1,
    )

    path.write_text(text, encoding="utf-8")
    ok(f"ARCHITECTURE.md updated ({updated} line counts, test count={test_count})")


def update_readme_md(test_count: int, ty_count: int) -> None:
    """Update test count, ty badge, and rounded UI line counts in README.md."""
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    test_files = count_test_files()

    # README uses two places for the count: the <details> summary and the
    # body line right below it.
    text = re.sub(
        r"<strong>Test coverage</strong> - \d+ tests, \d+% overall",
        f"<strong>Test coverage</strong> - {test_count} tests, 67% overall",
        text,
        count=1,
    )
    text = re.sub(
        r"\d+ tests across \d+ test files\.",
        f"{test_count} tests across {test_files} test files.",
        text,
        count=1,
    )

    # ty badge: count + color track how many diagnostics ty currently
    # reports.  Thresholds: 0-9 green, 10-19 yellow, 20+ red.
    color = ty_badge_color(ty_count)
    text = re.sub(
        r"badge/ty-\d+%20issues-[a-z]+",
        f"badge/ty-{ty_count}%20issues-{color}",
        text,
        count=1,
    )

    # Rounded UI line counts. Round to nearest 50 for stability.
    def rounded(rel: str) -> int:
        return round(count_lines(rel) / 50) * 50

    app_lines = rounded("src/termapy/app.py")
    proto_debug_lines = rounded("src/termapy/proto_debug.py")
    dialogs_lines = rounded("src/termapy/dialogs.py")

    text = re.sub(
        r"`app\.py` \(~\d+ lines\), `proto_debug\.py` \(~\d+ lines\), and `dialogs\.py` \(~\d+ lines\)",
        f"`app.py` (~{app_lines} lines), `proto_debug.py` (~{proto_debug_lines} lines), and `dialogs.py` (~{dialogs_lines} lines)",
        text,
        count=1,
    )

    path.write_text(text, encoding="utf-8")
    ok(
        f"README.md updated (tests={test_count}, ty={ty_count} ({color}), "
        f"app.py~{app_lines}, dialogs.py~{dialogs_lines})"
    )


# ── changelog ────────────────────────────────────────────────────────────────


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

    # Insert after the first line ("# Changelog")
    new_text = re.sub(
        r"(# Changelog\n+)",
        r"\1" + stub,
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

    total = 11

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
    ok("git state is clean and ready")

    step(2, "Cutting release branch...")
    cut_release_branch(version)

    step(3, "Bumping version files...")
    bump_pyproject(version)
    bump_mkdocs(version)
    refresh_uv_lock()

    step(4, "Updating doc counts (test count, ty count, line counts)...")
    test_count = count_tests()
    ty_count = count_ty_issues()
    update_architecture_md(test_count)
    update_readme_md(test_count, ty_count)

    step(5, "Updating config examples in docs...")
    from update_doc_configs import update_doc_configs
    updated = update_doc_configs()
    if updated:
        ok(f"updated {len(updated)} config example(s)")
    else:
        ok("all config examples current")

    step(6, "Inserting CHANGELOG stub...")
    insert_changelog_stub(version)

    step(7, "Running pytest...")
    run_pytest()

    step(8, "Running tox (multi-version)...")
    run_tox()

    step(9, "Building HTML help with zensical...")
    run_zensical_build()

    step(10, "Committing HTML rebuild...")
    commit_html_rebuild(version)

    step(11, "Committing release...")
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
