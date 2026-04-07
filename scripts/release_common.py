"""Shared helpers for release_prep.py and release_publish.py.

Stdlib only. No third-party deps.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Repo root is the parent of scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent

# ── ANSI colors ────────────────────────────────────────────────────────────────
# Plain ASCII fallback if stdout is not a tty (CI logs, redirected output)
if sys.stdout.isatty():
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
else:
    GREEN = RED = YELLOW = BLUE = BOLD = RESET = ""


def info(msg: str) -> None:
    print(f"{BLUE}==>{RESET} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}OK {RESET} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}!! {RESET} {msg}")


def die(msg: str, code: int = 1) -> None:
    print(f"{RED}FAIL{RESET} {msg}", file=sys.stderr)
    sys.exit(code)


# ── subprocess helpers ────────────────────────────────────────────────────────


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a command. By default, fail loudly if it returns nonzero.

    capture=True returns stdout in .stdout; otherwise output is streamed to
    the parent terminal.
    """
    cwd = cwd or REPO_ROOT
    return subprocess.run(
        cmd,
        check=check,
        cwd=cwd,
        text=True,
        capture_output=capture,
    )


def run_out(cmd: list[str], *, cwd: Path | None = None) -> str:
    """Run a command and return its stdout, stripped. Fails loudly on nonzero."""
    return run(cmd, capture=True, cwd=cwd).stdout.strip()


# ── version validation ───────────────────────────────────────────────────────


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def validate_version(version: str) -> None:
    """Reject anything that isn't N.N.N. No RCs, no suffixes, no leading 'v'."""
    if version.startswith("v"):
        die(f"version must not start with 'v' (got {version!r}). Use '0.53.0', not 'v0.53.0'.")
    if not VERSION_RE.match(version):
        die(f"version must be N.N.N (got {version!r}). No release candidates, no suffixes.")


# ── git state checks ──────────────────────────────────────────────────────────


def current_branch() -> str:
    return run_out(["git", "branch", "--show-current"])


def working_tree_clean() -> bool:
    return run_out(["git", "status", "--porcelain"]) == ""


def assert_clean_tree() -> None:
    if not working_tree_clean():
        die("working tree is dirty. Commit or stash before running this script.")


def assert_on_main() -> None:
    branch = current_branch()
    if branch != "main":
        die(f"must run from 'main' branch, currently on {branch!r}.")


def assert_main_in_sync_with_origin() -> None:
    """Local main must be at the same commit as origin/main."""
    run(["git", "fetch", "origin", "main"])
    local = run_out(["git", "rev-parse", "main"])
    remote = run_out(["git", "rev-parse", "origin/main"])
    if local != remote:
        die(
            "local 'main' is not in sync with 'origin/main'. "
            "Pull, push, or rebase before releasing."
        )


def tag_exists(tag: str) -> bool:
    """True if a local or remote tag with this name already exists."""
    local = run_out(["git", "tag", "--list", tag])
    if local:
        return True
    # Check remote tags too
    remote = run_out(["git", "ls-remote", "--tags", "origin", tag])
    return bool(remote)


def assert_tag_does_not_exist(version: str) -> None:
    tag = f"v{version}"
    if tag_exists(tag):
        die(f"tag {tag!r} already exists locally or on origin.")


def last_tag() -> str:
    """Most recent v* tag, sorted by version."""
    return run_out(["git", "describe", "--tags", "--abbrev=0", "--match", "v*"])


# ── tooling checks ────────────────────────────────────────────────────────────


def assert_tool_available(name: str) -> None:
    """Fail if the named CLI tool is not on PATH."""
    try:
        subprocess.run(
            [name, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        die(f"required tool not available: {name!r}. Install it before running this script.")


def assert_gh_authenticated() -> None:
    """Fail if `gh` is not logged in."""
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die("`gh` is not authenticated. Run `gh auth login` first.")
