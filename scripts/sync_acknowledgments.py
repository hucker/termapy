"""Sync the acknowledgments markdown into credits.py.

Single source of truth: ``src/termapy/help/acknowledgments.md``.
This script reads that file and rewrites the generated block in
``src/termapy/builtins/commands/credits.py`` between the BEGIN /
END GENERATED sentinels.

Usage:
    python scripts/sync_acknowledgments.py          # writes if drifted
    python scripts/sync_acknowledgments.py --check  # exit 1 if drifted

The --check mode is what ``tests/test_credits_sync.py`` uses (via
the function ``render_block``) so CI fails fast on any drift; it
also makes the script useful as a pre-commit hook.

stdlib-only.  No regex on the markdown body itself -- we splice the
file verbatim between the sentinels.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = REPO_ROOT / "src" / "termapy" / "help" / "acknowledgments.md"
PY_PATH = REPO_ROOT / "src" / "termapy" / "builtins" / "commands" / "credits.py"

_BEGIN = "# ── BEGIN GENERATED (sync via scripts/sync_acknowledgments.py) ────────────\n"
_END = "# ── END GENERATED ─────────────────────────────────────────────────────────"

# Pattern matches everything from BEGIN line through END line (inclusive),
# non-greedy so multiple generated blocks in one file (unlikely) don't merge.
_BLOCK_RE = re.compile(
    re.escape(_BEGIN) + r".*?" + re.escape(_END),
    re.DOTALL,
)


def render_block(md_text: str) -> str:
    """Build the generated block content from the markdown body.

    Returns the full text that should appear between (and including)
    the BEGIN and END sentinels.  Callers do a string-equality compare
    against the current file contents to decide if a write is needed.

    Embedding strategy: triple-quoted raw-ish string.  The markdown
    contains backticks but no embedded triple quotes, so a plain
    ``\"\"\"`` literal is safe.  We assert that on entry rather than
    handle the edge case implicitly.
    """
    if '"""' in md_text:
        raise ValueError(
            "acknowledgments.md contains a triple-quote sequence; the "
            "embedded Python string literal cannot represent it without "
            "escaping.  Refactor the markdown or the sync script."
        )
    return (
        _BEGIN
        + "# Source: src/termapy/help/acknowledgments.md\n"
        + "# Do not edit this block by hand -- edit the markdown and re-run the sync.\n"
        + '_ACKNOWLEDGMENTS = """'
        + md_text
        + '"""\n'
        + _END
    )


def rewrite_credits(py_text: str, new_block: str) -> str:
    """Return ``py_text`` with the generated block replaced by ``new_block``.

    Raises if the sentinels are missing or appear more than once --
    those are both ``credits.py`` was hand-edited in a way the sync
    can't safely handle.
    """
    matches = _BLOCK_RE.findall(py_text)
    if not matches:
        raise RuntimeError(
            f"BEGIN/END sentinels not found in {PY_PATH.name}; "
            "the file may have been hand-edited.  Restore the "
            "sentinels and re-run."
        )
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple generated blocks in {PY_PATH.name}; expected one."
        )
    return _BLOCK_RE.sub(lambda _m: new_block, py_text, count=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if credits.py is out of sync (do not modify).",
    )
    args = parser.parse_args(argv)

    md_text = MD_PATH.read_text(encoding="utf-8")
    py_text = PY_PATH.read_text(encoding="utf-8")
    new_block = render_block(md_text)
    new_py_text = rewrite_credits(py_text, new_block)

    if new_py_text == py_text:
        print(f"OK  {PY_PATH.relative_to(REPO_ROOT)} is in sync")
        return 0

    if args.check:
        print(
            f"FAIL {PY_PATH.relative_to(REPO_ROOT)} is out of sync with "
            f"{MD_PATH.relative_to(REPO_ROOT)}.\n"
            f"     Run: python scripts/sync_acknowledgments.py",
            file=sys.stderr,
        )
        return 1

    PY_PATH.write_text(new_py_text, encoding="utf-8")
    print(f"OK  {PY_PATH.relative_to(REPO_ROOT)} synced from {MD_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
