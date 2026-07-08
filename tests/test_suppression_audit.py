"""Tests for scripts/suppression_audit.py -- the release-prep suppression gate.

The classifier is the load-bearing part: it decides whether a suppression is
bare (no rule code) or reason-less, which is what release_prep hard-fails on.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from suppression_audit import (  # noqa: E402 -- scripts/ prepended to sys.path above
    classify,
    counts_by_form,
    scan_tree,
)


class TestClassify:
    """The bare/reason-less decision on individual source lines."""

    def test_noqa_coded_with_reason_passes(self):
        # Act
        s = classify("f.py", 1, "x = f()  # noqa: BLE001 -- boundary thread")

        # Assert
        assert s is not None, "recognized as a suppression"
        assert s.form == "noqa", "classified as noqa"
        assert s.code == "BLE001", "rule code extracted"
        assert s.has_reason, "trailing reason detected"
        assert not s.is_offender, "coded + reason passes the gate"

    def test_noqa_bare_is_offender(self):
        # Act
        s = classify("f.py", 1, "x = f()  # noqa")

        # Assert
        assert s is not None and s.is_bare, "bare noqa carries no rule code"
        assert s.is_offender, "a bare suppression fails the gate"

    def test_noqa_coded_but_reasonless_is_offender(self):
        # Act
        s = classify("f.py", 1, "import x  # noqa: F401")

        # Assert
        assert s is not None and s.code == "F401", "code present"
        assert not s.has_reason, "no trailing reason"
        assert s.is_offender, "a reason-less suppression fails the gate"

    def test_ty_ignore_coded_with_reason_passes(self):
        # Act
        s = classify("f.py", 1, "y()  # ty: ignore[unresolved-import] -- optional extra")

        # Assert
        assert s is not None and s.form == "ty-ignore", "classified as ty-ignore"
        assert s.code == "unresolved-import" and s.has_reason, "code + reason"
        assert not s.is_offender, "coded + reason passes"

    def test_ty_ignore_bare_fails_even_with_reason(self):
        # Act -- a reason but no bracketed code
        s = classify("f.py", 1, "y()  # ty: ignore -- I have a reason")

        # Assert
        assert s is not None and s.is_bare, "no bracketed code = bare"
        assert s.is_offender, "the gate still requires a specific rule code"

    def test_type_ignore_is_distinct_from_ty_ignore(self):
        # Act -- 'type:' must not be misread as 'ty:'
        s = classify("f.py", 1, "z = w  # type: ignore[assignment] -- narrowing")

        # Assert
        assert s is not None and s.form == "type-ignore", "type: != ty:"
        assert s.code == "assignment", "bracketed code extracted"

    def test_pragma_never_bare_but_needs_a_reason(self):
        # Act
        bare = classify("f.py", 1, "except X:  # pragma: no cover")
        good = classify("f.py", 1, "except X:  # pragma: no cover -- stdlib on 3.11+")

        # Assert
        assert bare is not None and not bare.is_bare, "pragma has no rule-code concept"
        assert bare.is_offender, "a reason-less pragma still fails the gate"
        assert good is not None and not good.is_offender, "pragma + reason passes"

    def test_noqa_multiple_codes(self):
        # Act
        s = classify("f.py", 1, "x  # noqa: E501,F401 -- long vendored import line")

        # Assert
        assert s is not None and s.code == "E501,F401", "comma-joined codes kept"
        assert not s.is_offender, "coded + reason passes"

    def test_plain_lines_are_not_suppressions(self):
        # Assert -- ordinary comments and code produce nothing
        assert classify("f.py", 1, "x = 1  # just a comment") is None, "plain comment"
        assert classify("f.py", 1, "x = 1") is None, "no comment at all"


class TestScanTree:
    """The whole-tree scan behind the release count."""

    def test_scan_excludes_vendor_and_finds_real_suppressions(self):
        # Act
        found = scan_tree(_REPO_ROOT)
        by_form = counts_by_form(found)

        # Assert -- the project genuinely has noqa suppressions, and none of
        # the reported ones live under the vendored tree.
        assert by_form.get("noqa", 0) >= 1, "real noqa suppressions are counted"
        assert all("vendor" not in s.file.split("/") for s in found), (
            "vendored suppressions are excluded from the count"
        )
