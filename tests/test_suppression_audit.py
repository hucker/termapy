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
    gate_offenders,
    reason_above,
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


class TestPrecedingReason:
    """The gate accepts a reason inline OR on the comment line directly above."""

    def _write(self, tmp_path, text):
        (tmp_path / "sample.py").write_text(text, encoding="utf-8")

    def test_reason_on_line_above_clears_the_gate(self, tmp_path):
        # Arrange -- coded suppression, no inline reason, reason comment above
        self._write(tmp_path, "# deferred to dodge an import cycle\nx = y  # noqa: E402\n")
        supp = classify("sample.py", 2, "x = y  # noqa: E402")

        # Act
        # Assert
        assert reason_above(supp, tmp_path), "the comment above is the reason"
        assert gate_offenders([supp], tmp_path) == [], "reason-above clears the gate"

    def test_code_line_above_is_not_a_reason(self, tmp_path):
        # Arrange -- the line above is code, not a comment
        self._write(tmp_path, "x = 1\ny = z  # noqa: E402\n")
        supp = classify("sample.py", 2, "y = z  # noqa: E402")

        # Assert
        assert not reason_above(supp, tmp_path), "code above is not a reason"
        assert gate_offenders([supp], tmp_path) == [supp], "still an offender"

    def test_suppression_above_does_not_excuse(self, tmp_path):
        # Arrange -- the line above is itself a suppression, not a reason
        self._write(tmp_path, "a = f()  # noqa: F401\nb = g()  # noqa: E402\n")
        supp = classify("sample.py", 2, "b = g()  # noqa: E402")

        # Assert -- stacked suppressions can't excuse each other
        assert not reason_above(supp, tmp_path), "a suppression above isn't a reason"

    def test_bare_fails_even_with_a_reason_above(self, tmp_path):
        # Arrange -- reason comment above, but the suppression itself is bare
        self._write(tmp_path, "# explanation here\nz = h()  # type: ignore\n")
        supp = classify("sample.py", 2, "z = h()  # type: ignore")

        # Assert -- a missing rule code fails regardless of a reason above
        assert supp.is_bare, "no bracketed code = bare"
        assert gate_offenders([supp], tmp_path) == [supp], "bare fails the gate"
