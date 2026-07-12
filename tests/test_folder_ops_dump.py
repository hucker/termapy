"""Containment tests for the shared ``.dump`` folder handler.

``/cap.dump``, ``/proto.dump``, ``/run.dump``, ``/plugin.dump`` all
share ``folder_ops._make_dump_handler``.  The handler reads a file NAME
from the folder -- and must refuse a name that escapes the folder (an
absolute path or ``../`` traversal), which would otherwise be an
arbitrary-file read for an MCP client.  ``/show`` is the command for
reading files elsewhere, and it is capability-gated.
"""

from __future__ import annotations

import json

from termapy.folder_ops import _make_dump_handler
from termapy.plugins import IOHandle, PluginContext


def _ctx(cfg_file):
    """Minimal ctx: a config_path (so _folder_path resolves) + captured io."""
    output: list = []
    ctx = PluginContext(
        io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
        config_path=str(cfg_file),
    )
    return ctx, output


def _setup(tmp_path):
    """Create <cfg_dir>/cap/adc.csv and a secret one level up, outside cap/."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "test.cfg"
    cfg_file.write_text(json.dumps({"port": ""}), encoding="utf-8")
    cap_dir = cfg_dir / "cap"
    cap_dir.mkdir()
    (cap_dir / "adc.csv").write_text("1,2,3\n", encoding="utf-8")
    secret = cfg_dir / "secret.txt"  # sibling of cap/, NOT inside it
    secret.write_text("API_KEY=hunter2", encoding="utf-8")
    return cfg_file, cap_dir, secret


class TestDumpContainment:
    def test_dumps_file_inside_folder(self, tmp_path):
        # Arrange
        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, output = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, "adc.csv")
        # Assert -- normal use is unaffected.
        assert result.success, "a file inside cap/ dumps normally"
        assert "1,2,3" in result.value, "contents returned for scripting"

    def test_absolute_path_refused(self, tmp_path):
        # Arrange -- caller supplies an absolute path to a secret outside
        # cap/.  `cap_dir / abs_path` == abs_path in pathlib, so without
        # containment this would read it.
        cfg_file, _cap, secret = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, str(secret))
        # Assert
        assert not result.success, "absolute path is refused"
        assert "escapes" in result.error, "error explains containment"
        assert "hunter2" not in (result.value or ""), "secret never read"

    def test_parent_traversal_refused(self, tmp_path):
        # Arrange -- ../secret.txt climbs out of cap/ into the cfg dir.
        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, "../secret.txt")
        # Assert
        assert not result.success, "../ traversal is refused"
        assert "escapes" in result.error, "error explains containment"

    def test_deep_traversal_refused(self, tmp_path):
        # Arrange -- many-level climb toward the filesystem root.
        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, "../../../../../../etc/passwd")
        # Assert
        assert not result.success, "deep traversal is refused"

    def test_missing_file_inside_folder_still_reports_not_found(self, tmp_path):
        # Arrange -- containment must not mask the ordinary not-found path
        # for a legitimate (contained) name.
        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, "nope.csv")
        # Assert
        assert not result.success, "missing contained file fails"
        assert "not found" in result.error.lower(), "distinct not-found message"
