"""Sync the dependency registry -> acknowledgments.md -> credits.py.

Two generated artifacts, one source of truth
(``src/termapy/credits_data.py``, the ``CREDITS`` table):

1. The "Other runtime dependencies" bullets in
   ``src/termapy/help/acknowledgments.md`` are regenerated between the
   ``<!-- deps:start -->`` / ``<!-- deps:end -->`` markers from the
   ``runtime`` and ``optional`` records.  The prose around them
   (reveng, pyserial, Textual, the vendored packages) stays hand-written.
2. The whole markdown page is then embedded into
   ``src/termapy/builtins/commands/credits.py`` between the BEGIN / END
   GENERATED sentinels, as before, so the wheel ships zero markdown.

Usage:
    python scripts/sync_acknowledgments.py          # writes if drifted
    python scripts/sync_acknowledgments.py --check  # exit 1 if drifted

The --check mode is what ``tests/test_credits_sync.py`` uses so CI fails
fast on any drift; it also makes the script useful as a pre-commit hook.

stdlib plus the registry module itself (imported from ``src/``, pure
data).  No regex on the markdown body -- the generated block is spliced
between literal markers, the page is spliced verbatim into the module.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = REPO_ROOT / "src" / "termapy" / "help" / "acknowledgments.md"
PY_PATH = REPO_ROOT / "src" / "termapy" / "builtins" / "commands" / "credits.py"

sys.path.insert(0, str(REPO_ROOT / "src"))
from termapy.credits_data import Credit, credits_of  # noqa: E402 -- path set above

_BEGIN = "# ── BEGIN GENERATED (sync via scripts/sync_acknowledgments.py) ────────────\n"
_END = "# ── END GENERATED ─────────────────────────────────────────────────────────"

# Pattern matches everything from BEGIN line through END line (inclusive),
# non-greedy so multiple generated blocks in one file (unlikely) don't merge.
_BLOCK_RE = re.compile(
    re.escape(_BEGIN) + r".*?" + re.escape(_END),
    re.DOTALL,
)

_DEPS_START = "<!-- deps:start"
_DEPS_END = "<!-- deps:end -->"
_DEPS_RE = re.compile(
    re.escape(_DEPS_START) + r"[^\n]*\n.*?" + re.escape(_DEPS_END),
    re.DOTALL,
)
_WRAP = 70


def _bullet(credit: Credit) -> str:
    """One markdown bullet, wrapped like the hand-written ones were.

    An ``optional`` record names its extra right after the link.
    """
    extra = f" (`{credit.extra}` extra)" if credit.kind == "optional" else ""
    head = f"- [**{credit.package}**]({credit.url}){extra} -- {credit.note}"
    return textwrap.fill(head, width=_WRAP, subsequent_indent="  ")


def render_deps_block(start_line: str) -> str:
    """The generated markdown between (and including) the deps markers.

    ``start_line`` is the existing ``<!-- deps:start ... -->`` line, kept
    verbatim so its explanatory comment survives regeneration.  A record
    with an empty ``note`` is credited in prose elsewhere on the page
    (Textual, prompt_toolkit have their own sections) and gets no bullet.
    """
    lines = [start_line]
    lines += [_bullet(credit) for credit in credits_of("runtime") if credit.note]
    optional = [credit for credit in credits_of("optional") if credit.note]
    if optional:
        lines.append("")
        lines.append("Optional extras (`pip install termapy[<extra>]`):")
        lines.append("")
        lines += [_bullet(credit) for credit in optional]
    lines.append(_DEPS_END)
    return "\n".join(lines)


def rewrite_markdown(md_text: str) -> str:
    """Return ``md_text`` with the deps block regenerated from the registry."""
    match = _DEPS_RE.search(md_text)
    if match is None:
        raise RuntimeError(
            f"deps:start / deps:end markers not found in {MD_PATH.name}; "
            "restore them and re-run."
        )
    start_line = match.group(0).split("\n", 1)[0]
    return _DEPS_RE.sub(lambda _m: render_deps_block(start_line), md_text, count=1)


def render_block(md_text: str) -> str:
    """Build the generated block content for credits.py from the markdown body.

    Returns the full text that should appear between (and including)
    the BEGIN and END sentinels.  Callers do a string-equality compare
    against the current file contents to decide if a write is needed.

    Embedding strategy: triple-quoted string.  The markdown contains
    backticks but no embedded triple quotes, so a plain ``\"\"\"`` literal
    is safe.  We assert that on entry rather than handle the edge case
    implicitly.
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
        help="Exit 1 if acknowledgments.md or credits.py is out of sync (do not modify).",
    )
    args = parser.parse_args(argv)

    md_text = MD_PATH.read_text(encoding="utf-8")
    py_text = PY_PATH.read_text(encoding="utf-8")
    new_md_text = rewrite_markdown(md_text)
    new_py_text = rewrite_credits(py_text, render_block(new_md_text))

    md_changed = new_md_text != md_text
    py_changed = new_py_text != py_text
    if not md_changed and not py_changed:
        print(f"OK  {MD_PATH.name} and {PY_PATH.relative_to(REPO_ROOT)} are in sync")
        return 0

    if args.check:
        stale = [name for name, changed in ((MD_PATH.name, md_changed), (PY_PATH.name, py_changed)) if changed]
        print(
            f"FAIL {', '.join(stale)} out of sync with the dependency registry.\n"
            f"     Run: python scripts/sync_acknowledgments.py",
            file=sys.stderr,
        )
        return 1

    if md_changed:
        MD_PATH.write_text(new_md_text, encoding="utf-8")
        print(f"OK  {MD_PATH.name} deps block regenerated from credits_data.py")
    if py_changed:
        PY_PATH.write_text(new_py_text, encoding="utf-8")
        print(f"OK  {PY_PATH.relative_to(REPO_ROOT)} synced from {MD_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
