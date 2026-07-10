"""Suppression-pragma audit for release_prep.

Counts lint / type / coverage suppressions in ``src/termapy`` (excluding the
vendored ``vendor/`` tree) and gates newly-added ones.  The philosophy: a
suppression is sometimes the right call, but it must never be the *easy* call.
So every suppression introduced since the previous release must carry a
specific rule code AND a reason -- inline (``-- why``), or on the comment line
directly above it -- otherwise release_prep aborts.

Forms recognized:
  # ty: ignore[code]      (ty type checker)
  # type: ignore[code]    (blanket/mypy-namespace -- bare form flagged)
  # noqa: CODE            (ruff)
  # pragma: no cover      (coverage -- no rule code, but a reason is required)

Stdlib-only, matching the release-scripts convention.  Run standalone for an
ad-hoc audit::

    python scripts/suppression_audit.py            # count + list
    python scripts/suppression_audit.py --since v0.72.0   # gate the diff
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_REL = "src/termapy"
_VENDOR_PART = "vendor"

# One matcher per form.  Order matters: ``ty:`` is checked before ``type:``
# only to be explicit -- the ``ty:``/``type:`` colon placement already makes
# them mutually exclusive.  ``[^\]]*`` captures the bracketed code (ty/type);
# noqa uses ``: CODE[,CODE]``; pragma carries no rule code.
_FORMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ty-ignore", re.compile(r"#\s*ty:\s*ignore(?:\[(?P<code>[^\]]*)\])?(?P<rest>.*)$")),
    ("type-ignore", re.compile(r"#\s*type:\s*ignore(?:\[(?P<code>[^\]]*)\])?(?P<rest>.*)$")),
    ("noqa", re.compile(r"#\s*noqa(?::\s*(?P<code>[A-Z][A-Z0-9]+(?:\s*,\s*[A-Z][A-Z0-9]+)*))?(?P<rest>.*)$")),
    ("pragma", re.compile(r"#\s*pragma:\s*no\s*cover(?P<rest>.*)$")),
)


@dataclass(frozen=True)
class Suppression:
    """One suppression pragma found on a source line."""

    file: str          # repo-relative path, forward slashes
    line: int
    form: str          # ty-ignore | type-ignore | noqa | pragma
    code: str | None   # rule code(s), or None if none was given
    has_reason: bool   # is there explanatory text after the code?

    @property
    def is_bare(self) -> bool:
        """True if the form supports a rule code but none was given.

        ``pragma: no cover`` has no rule-code concept, so it is never bare.
        """
        return self.form != "pragma" and self.code is None

    @property
    def is_offender(self) -> bool:
        """A new suppression fails the gate if it is bare OR reason-less."""
        return self.is_bare or not self.has_reason

    def describe(self) -> str:
        problem = "no rule code" if self.is_bare else "no reason"
        if self.is_bare and not self.has_reason:
            problem = "no rule code and no reason"
        return f"{self.file}:{self.line}  [{self.form}]  -- {problem}"


def classify(file: str, line_no: int, source_line: str) -> Suppression | None:
    """Return a Suppression if ``source_line`` carries one, else None."""
    for form, rx in _FORMS:
        m = rx.search(source_line)
        if not m:
            continue
        code = m.groupdict().get("code")
        code = code.strip() if code else None
        rest = m.groupdict().get("rest") or ""
        # A reason is any alphabetic text trailing the code (the project
        # convention is ``-- why``); punctuation/whitespace alone doesn't count.
        has_reason = bool(re.search(r"[A-Za-z]", rest))
        return Suppression(file, line_no, form, code or None, has_reason)
    return None


def reason_above(supp: Suppression, repo_root: Path = REPO_ROOT) -> bool:
    """True if the comment line directly above the suppression is a reason.

    A long suppression line can't always fit an inline ``-- reason`` under the
    line limit, so the repo convention is to put the justification on the
    comment line just above (e.g. app.py's deferred-import E402).  That line
    must be a plain comment with words -- and must NOT itself be a suppression,
    so two stacked suppressions can't excuse each other.
    """
    path = repo_root / supp.file
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    idx = supp.line - 2  # 0-based index of the line above (supp.line is 1-based)
    if idx < 0 or idx >= len(lines):
        return False
    above = lines[idx].strip()
    if not above.startswith("#") or not re.search(r"[A-Za-z]", above):
        return False
    return classify(supp.file, idx + 1, lines[idx]) is None


def gate_offenders(
    supps: list[Suppression], repo_root: Path = REPO_ROOT
) -> list[Suppression]:
    """Suppressions that fail the gate.

    A suppression fails if it is bare (no rule code -- a reason can't excuse a
    missing code), or if it carries no reason either inline or on the comment
    line directly above it.
    """
    offenders: list[Suppression] = []
    for s in supps:
        if s.is_bare or (not s.has_reason and not reason_above(s, repo_root)):
            offenders.append(s)
    return offenders


def scan_tree(repo_root: Path = REPO_ROOT) -> list[Suppression]:
    """Every suppression in src/termapy, excluding the vendored tree."""
    src = repo_root / SRC_REL
    found: list[Suppression] = []
    for py in sorted(src.rglob("*.py")):
        if _VENDOR_PART in py.relative_to(src).parts:
            continue
        rel = py.relative_to(repo_root).as_posix()
        for i, source_line in enumerate(
            py.read_text(encoding="utf-8").splitlines(), start=1
        ):
            s = classify(rel, i, source_line)
            if s is not None:
                found.append(s)
    return found


def added_since(tag: str, repo_root: Path = REPO_ROOT) -> list[Suppression]:
    """Suppressions on lines ADDED since ``tag`` (git diff tag..HEAD).

    Only added (``+``) lines under src/termapy (excluding vendor) are
    considered, so pre-existing suppressions in a touched file are ignored
    -- the gate is about what a change *introduces*.
    """
    diff = subprocess.run(
        ["git", "diff", f"{tag}..HEAD", "--unified=0", "--", SRC_REL],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout
    added: list[Suppression] = []
    cur_file: str | None = None
    new_lineno = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            cur_file = path[2:] if path.startswith("b/") else path
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if cur_file and _VENDOR_PART not in cur_file.split("/"):
                s = classify(cur_file, new_lineno, raw[1:])
                if s is not None:
                    added.append(s)
            new_lineno += 1
        # ``-`` and metadata lines don't advance the new-file line counter.
    return added


def previous_tag(repo_root: Path = REPO_ROOT) -> str | None:
    """Most recent tag reachable from HEAD, or None if the repo has none."""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        cwd=repo_root, capture_output=True, text=True,
    )
    tag = result.stdout.strip()
    return tag or None


def counts_by_form(supps: list[Suppression]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in supps:
        out[s.form] = out.get(s.form, 0) + 1
    return out


def suppression_status(supp: Suppression, repo_root: Path = REPO_ROOT) -> str:
    """One-token remediation status for the --list / release display."""
    if supp.is_bare:
        return "BARE"
    if supp.has_reason:
        return "ok"
    if reason_above(supp, repo_root):
        return "ok(above)"
    return "NO-REASON"


def format_list(supps: list[Suppression], repo_root: Path = REPO_ROOT) -> list[str]:
    """Render one aligned line per suppression: location, form[code], status."""
    if not supps:
        return []
    loc_w = max(len(f"{s.file}:{s.line}") for s in supps)
    tag_w = max(len(f"{s.form}[{s.code or '-'}]") for s in supps)
    lines: list[str] = []
    for s in supps:
        loc = f"{s.file}:{s.line}".ljust(loc_w)
        tag = f"{s.form}[{s.code or '-'}]".ljust(tag_w)
        lines.append(f"  {loc}  {tag}  {suppression_status(s, repo_root)}")
    return lines


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since", metavar="TAG",
        help="Gate: fail if suppressions added since TAG are bare or reason-less.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print every suppression with its file:line, form, and status.",
    )
    args = parser.parse_args(argv)

    total = scan_tree()
    by_form = counts_by_form(total)
    print(f"Suppressions in {SRC_REL} (excl. vendor): {len(total)}")
    for form in ("ty-ignore", "type-ignore", "noqa", "pragma"):
        if by_form.get(form):
            print(f"  {form:<12} {by_form[form]}")

    if args.list:
        print()
        for line in format_list(total):
            print(line)

    if args.since:
        offenders = gate_offenders(added_since(args.since))
        if offenders:
            print(f"\nGATE FAILED: {len(offenders)} new suppression(s) "
                  f"since {args.since} lack a rule code and/or a reason:")
            for s in offenders:
                print(f"  {s.describe()}")
            print("\nEvery suppression must name its rule and say why "
                  "(e.g. `# noqa: BLE001 -- boundary thread`).")
            return 1
        print(f"\nGate OK: no bare/reason-less suppressions added since {args.since}.")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
