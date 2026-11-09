"""The three tool gates as tests: ruff finds nothing, ruff format would change nothing, ty finds nothing.

The pre-commit hook and ``release_prep`` run the same three commands and
refuse otherwise; here they are tests, so every ``pytest`` run, CI included,
holds the same bar and the registry can show the bar as pinned (registry
``ruff-clean``, ``ruff-formatted``, ``ty-clean``).  Each tool runs from this
interpreter's environment over the whole project, with no paths, so the
excludes in ``pyproject.toml`` are the scope, as they are for the hook.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.slow  # three subprocesses, about three seconds together


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a tool from this environment over the whole project."""
    return subprocess.run(
        [sys.executable, "-m", *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
        check=False,
    )


def _verdict(result: subprocess.CompletedProcess[str]) -> str:
    return f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"


@pytest.mark.rule("ruff-clean")
def test_ruff_finds_nothing():
    """``ruff check`` over the project reports zero findings under the pinned rule set."""
    # Act
    result = _run("ruff", "check")

    # Assert
    assert result.returncode == 0, (
        "ruff found issues; run: uv run ruff check --fix, then fix or suppress the rest "
        f"with a rule code and a reason\n{_verdict(result)}"
    )


@pytest.mark.rule("ruff-formatted")
def test_ruff_format_finds_nothing_to_change():
    """``ruff format --check --no-cache`` finds every file laid out as the formatter lays it out."""
    # Act -- --no-cache: the cache records what ruff wrote as formatted, and
    # a second pass can still move a file (two did, 2026-10-17)
    result = _run("ruff", "format", "--check", "--no-cache")

    # Assert
    assert result.returncode == 0, f"unformatted files; run: uv run ruff format\n{_verdict(result)}"


@pytest.mark.rule("ty-clean")
def test_ty_finds_nothing():
    """``ty check`` over the project, tests and scripts included, reports zero diagnostics."""
    # Act
    result = _run("ty", "check")

    # Assert
    assert result.returncode == 0, (
        "ty found diagnostics; fix each, or suppress it with `# ty: ignore[rule] -- why`; "
        f"a missing optional extra reads as an unresolved import, so sync with --all-extras\n"
        f"{_verdict(result)}"
    )
