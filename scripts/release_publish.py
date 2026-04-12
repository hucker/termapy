"""Release publish: merge release branch to main, tag, push, GitHub release.

Run this after release_prep.py and after you've reviewed/edited CHANGELOG.md.

Usage:
    python scripts/release_publish.py --yes

What it does:
    1. Verify on release/v<version> branch (parses version from branch name)
    2. Verify working tree is clean
    3. Verify CHANGELOG has no TODO markers left from the stub
    4. Verify the v<version> tag does not yet exist
    5. Require --yes to proceed (no interactive prompt)
    6. Checkout main, merge release branch with --no-ff
    7. Create tag v<version>
    8. Push main, tag, and release branch to origin
    9. Create GitHub release with notes extracted from CHANGELOG.md
    10. Build and publish to PyPI via uv
    11. Print summary

Aborts loudly on any failure. Never force-pushes. Never deletes branches.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_common import (  # noqa: E402
    REPO_ROOT,
    assert_clean_tree,
    assert_gh_authenticated,
    assert_tag_does_not_exist,
    assert_tool_available,
    current_branch,
    die,
    info,
    ok,
    run,
    run_out,
    validate_version,
    warn,
)


RELEASE_BRANCH_RE = re.compile(r"^release/v(\d+\.\d+\.\d+)$")


def parse_version_from_branch() -> str:
    branch = current_branch()
    m = RELEASE_BRANCH_RE.match(branch)
    if not m:
        die(
            f"must be on a release/vN.N.N branch, currently on {branch!r}. "
            f"Run release_prep.py first."
        )
    version = m.group(1)
    validate_version(version)
    return version


def assert_no_changelog_todos() -> None:
    path = REPO_ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    if "TODO" in text:
        die(
            "CHANGELOG.md still contains TODO markers from the stub. "
            "Edit it (and amend the release commit) before publishing."
        )
    ok("CHANGELOG.md has no TODO markers")


def assert_release_commit_present(version: str) -> None:
    """Last commit on the branch should be the release commit."""
    subject = run_out(["git", "log", "-1", "--pretty=%s"])
    expected = f"Release v{version}"
    if subject != expected:
        die(
            f"expected HEAD commit subject {expected!r}, got {subject!r}. "
            f"Did you amend after editing CHANGELOG?"
        )
    ok(f"HEAD is {expected!r}")


def extract_changelog_notes(version: str) -> str:
    """Extract the section for this version from CHANGELOG.md."""
    path = REPO_ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    # Match from "## <version> (...)" up to the next "## " or EOF
    pattern = re.compile(
        rf"^## {re.escape(version)} \([^)]+\)\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        die(f"could not find '## {version} (...)' section in CHANGELOG.md")
    notes = m.group(1).strip()
    if not notes:
        die(f"CHANGELOG section for {version} is empty")
    return notes


def merge_to_main(version: str, release_branch: str) -> None:
    info("Checking out main and merging release branch...")
    run(["git", "checkout", "main"])
    # Make sure main is still in sync (someone might have pushed in the interim)
    run(["git", "fetch", "origin", "main"])
    local = run_out(["git", "rev-parse", "main"])
    remote = run_out(["git", "rev-parse", "origin/main"])
    if local != remote:
        die(
            "local 'main' diverged from 'origin/main' since prep ran. "
            "Resolve manually before publishing."
        )
    run(
        [
            "git",
            "merge",
            "--no-ff",
            release_branch,
            "-m",
            f"Merge {release_branch}",
        ]
    )
    ok(f"merged {release_branch} into main")


def create_tag(version: str) -> None:
    tag = f"v{version}"
    run(["git", "tag", tag])
    ok(f"created tag {tag}")


def push_all(version: str, release_branch: str) -> None:
    tag = f"v{version}"
    info("Pushing main, tag, and release branch to origin...")
    run(["git", "push", "origin", "main"])
    run(["git", "push", "origin", tag])
    run(["git", "push", "origin", release_branch])
    ok("pushed main, tag, and release branch")


def create_github_release(version: str, notes: str) -> None:
    tag = f"v{version}"
    info(f"Creating GitHub release {tag}...")
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--title",
            tag,
            "--notes",
            notes,
        ]
    )
    ok(f"GitHub release {tag} created")


def _get_pypi_token() -> str:
    """Read PyPI token from UV_PUBLISH_TOKEN env var or .pypirc in repo root."""
    import configparser
    import os

    token = os.environ.get("UV_PUBLISH_TOKEN", "")
    if token:
        return token
    pypirc = REPO_ROOT / ".pypirc"
    if pypirc.exists():
        cfg = configparser.ConfigParser()
        cfg.read(str(pypirc))
        if cfg.has_section("pypi") and cfg.get("pypi", "username", fallback="") == "__token__":
            return cfg.get("pypi", "password", fallback="")
    die(
        "No PyPI token found. Set UV_PUBLISH_TOKEN env var or "
        "add a .pypirc file with [pypi] username=__token__ password=pypi-..."
    )
    return ""  # unreachable, keeps type checker happy


def build_and_publish_pypi(version: str) -> None:
    """Build sdist + wheel and publish to PyPI."""
    import shutil

    token = _get_pypi_token()
    dist_dir = REPO_ROOT / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    info(f"Building v{version} for PyPI...")
    run(["uv", "build"], cwd=str(REPO_ROOT))
    ok("built sdist and wheel")
    info("Publishing to PyPI...")
    run(["uv", "publish", "--token", token], cwd=str(REPO_ROOT))
    ok(f"v{version} published to PyPI")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required. Confirms you reviewed the diff and CHANGELOG and "
        "want to publish (push to origin + create GitHub release).",
    )
    args = parser.parse_args()

    if not args.yes:
        die("refusing to publish without --yes. Review the diff first, then re-run with --yes.")

    info("Checking environment...")
    assert_tool_available("git")
    assert_tool_available("gh")
    assert_tool_available("uv")
    assert_gh_authenticated()

    info("Checking branch and tree state...")
    version = parse_version_from_branch()
    release_branch = f"release/v{version}"
    info(f"Publishing v{version} from {release_branch}")

    assert_clean_tree()
    assert_tag_does_not_exist(version)
    assert_no_changelog_todos()
    assert_release_commit_present(version)

    notes = extract_changelog_notes(version)

    # ── Merge ────────────────────────────────────────────────────────────
    merge_to_main(version, release_branch)

    # ── Tag ──────────────────────────────────────────────────────────────
    create_tag(version)

    # ── Push ─────────────────────────────────────────────────────────────
    push_all(version, release_branch)

    # ── GitHub release ───────────────────────────────────────────────────
    create_github_release(version, notes)

    # ── PyPI ─────────────────────────────────────────────────────────────
    build_and_publish_pypi(version)

    # ── Done ─────────────────────────────────────────────────────────────
    print()
    ok(f"v{version} published")
    print()
    info("Post-release:")
    print(f"  - GitHub release: gh release view v{version} --web")
    print(f"  - PyPI: https://pypi.org/project/termapy/{version}/")
    print(f"  - You are now on main, at the merge commit.")
    print(f"  - The release branch {release_branch} is preserved (per project convention).")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("aborted by user", code=130)
