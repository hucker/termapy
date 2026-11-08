"""A file a person writes, saved with a UTF-8 byte-order mark, reads as if it had none.

Windows editors (and PowerShell's ``Set-Content -Encoding utf8``) start a
UTF-8 file with the three bytes ``EF BB BF``.  Read as plain UTF-8 they
become an invisible first character: a script's first line stopped being
a command (found 2026-11-07), and a config failed to parse as JSON.  Every
reader of a script, a config, a protocol file or a profile decodes with
``utf-8-sig``, which skips the mark when it is there and changes nothing
when it is not.
"""

from __future__ import annotations

import copy
import json

from termapy.config import load_config
from termapy.defaults import DEFAULT_CFG

_BOM = b"\xef\xbb\xbf"


def test_a_scripts_first_line_runs(cli, tmp_path, capsys):
    """A .run saved with a byte-order mark runs its first line as the command it is."""
    # Arrange
    script = tmp_path / "bom.run"
    script.write_bytes(_BOM + b"/print bom-first-line\n/print bom-second-line\n")
    capsys.readouterr()

    # Act
    cli.repl.dispatch_full(f"/run {script}")
    out = capsys.readouterr().out

    # Assert
    assert "bom-first-line" in out, f"the first line ran:\n{out}"
    assert "bom-second-line" in out, "and the rest after it"


def test_a_config_loads(tmp_path):
    """A .cfg saved with a byte-order mark loads, its values intact."""
    # Arrange
    cfg = copy.deepcopy(DEFAULT_CFG)
    cfg["title"] = "Gerät"  # a non-ASCII value: read as UTF-8, not the platform's code page
    path = tmp_path / "bom" / "bom.cfg"
    path.parent.mkdir()
    path.write_bytes(_BOM + json.dumps(cfg, ensure_ascii=False).encode("utf-8"))

    # Act
    loaded = load_config(str(path))

    # Assert
    assert loaded["title"] == "Gerät", "the mark is skipped and the text is UTF-8"
