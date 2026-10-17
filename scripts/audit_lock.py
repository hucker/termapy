"""Audit uv.lock for known vulnerabilities: every extra, every group.

The lock is what ships (the release commit includes it) and what a
scanner reads, so the audit runs against the lock itself, never against
whatever happens to be installed.  ``uv export`` writes the fully pinned
set -- all extras, all dependency groups -- and ``pip-audit`` checks each
pin against the PyPI advisory database without installing anything.

Usage:
    python scripts/audit_lock.py                    # exit 1 on any finding
    python scripts/audit_lock.py --ignore-vuln ID   # one reviewed as unreachable

``release_prep.py`` calls this as a hard gate right after the lock
refresh, and the CI ``audit`` job runs it on every push.  Stdlib only;
needs ``uv`` and ``uvx`` on PATH and network access to the advisory
database.

Why the lock and not the environment: an installed-environment audit
sees only what that environment installed.  The CI job this replaced ran
``uv sync`` without extras, so the ``mcp`` extra's packages were never
present to look at, and pointed pip-audit at a path with no dist-info in
it, so it audited zero packages and reported them all clean.  The lock
has no such blind spot.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

# Allow running as `python scripts/audit_lock.py`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_common import die, ok, run  # noqa: E402

# Pinned exactly, for the reason ruff and ty are: the gate's command-line
# surface must not move under a release.  Run through uvx rather than the
# dev group so the audit tool and its own dependency tree never join the
# lock they audit.  Bump deliberately, on a chore branch.
PIP_AUDIT = "pip-audit==2.10.1"


def audit_uv_lock(ignore_vulns: Sequence[str] = ()) -> None:
    """Export the lock and audit every pin; die on any finding.

    Args:
        ignore_vulns: Advisory ids (``CVE-...``, ``GHSA-...``, ``PYSEC-...``)
            passed through as ``--ignore-vuln``.  Each is a finding that
            was reviewed and judged unreachable; the release log keeps the
            decision because the ids sit on its command line.
    """
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        exported = run(
            [
                "uv",
                "export",
                "--all-extras",
                "--all-groups",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "-o",
                str(requirements),
            ],
            check=False,
            capture=True,
        )
        if exported.returncode != 0:
            die(f"uv export failed:\n{exported.stderr.strip()}")
        # The export carries hashes, which pip-audit takes as "fully pinned,
        # audit as written" -- so --disable-pip needs no resolver and no
        # --no-deps, and the tool has no hash warning to print.
        cmd = [
            "uvx",
            PIP_AUDIT,
            "-r",
            str(requirements),
            "--disable-pip",
            "--progress-spinner",
            "off",
        ]
        for vuln_id in ignore_vulns:
            cmd += ["--ignore-vuln", vuln_id]
        audited = run(cmd, check=False, capture=True)
    report = "\n".join(part.strip() for part in (audited.stdout, audited.stderr) if part.strip())
    if audited.returncode != 0:
        die(
            f"uv.lock audit failed:\n{report}\n"
            "  Move the package on a chore branch "
            "(uv lock --upgrade-package <name>), or, for a finding reviewed "
            "as unreachable, re-run with --ignore-vuln <ID>."
        )
    ignored = f", {len(ignore_vulns)} ignored" if ignore_vulns else ""
    ok(f"uv.lock audit clean ({PIP_AUDIT}{ignored})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ignore-vuln",
        action="append",
        default=[],
        metavar="ID",
        help="advisory id to ignore (CVE-..., GHSA-..., PYSEC-...); repeatable",
    )
    args = parser.parse_args(argv)
    audit_uv_lock(args.ignore_vuln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
