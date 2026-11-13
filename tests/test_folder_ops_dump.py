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
from pathlib import Path

import pytest

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
        """A name inside the folder still dumps and returns its contents as the value."""
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
        """An absolute path outside the folder is refused and the file is never read."""
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
        """A ../ name that climbs out of the folder is refused with an escape error."""
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
        """A many-level ../ climb toward the filesystem root is refused."""
        # Arrange -- many-level climb toward the filesystem root.
        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, _ = _ctx(cfg_file)
        handler = _make_dump_handler("cap", "*")
        # Act
        result = handler(ctx, "../../../../../../etc/passwd")
        # Assert
        assert not result.success, "deep traversal is refused"

    def test_missing_file_inside_folder_still_reports_not_found(self, tmp_path):
        """A missing contained name still gets not-found, not the containment error."""
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


class TestDumpName:
    @pytest.mark.rule("file-name-default-ext")
    def test_dump_takes_a_bare_stem(self, tmp_path):
        """/run.dump welcome prints run/welcome.run; the folder's .run is optional."""
        # Arrange -- the real /run.dump, so the folder's extension is wired in
        from termapy.folder_ops import build_folder_subcommands

        cfg_file, _cap, _secret = _setup(tmp_path)
        run_dir = cfg_file.parent / "run"
        run_dir.mkdir()
        (run_dir / "welcome.run").write_text("/print hi\n", encoding="utf-8")
        ctx, _ = _ctx(cfg_file)
        dump = build_folder_subcommands("run")["dump"].handler
        assert dump is not None, "run/ is dumpable"

        # Act
        result = dump(ctx, "welcome")

        # Assert
        assert result.success, result.error
        assert result.value == "/print hi\n", "the stem named run/welcome.run"


class TestShowName:
    """.show shares .dump's lookup: a named file opens, a name outside the folder never does."""

    @staticmethod
    def _show_ctx(cfg_file):
        """A ctx with a desktop whose viewer records what it is asked to open."""
        from termapy.plugins import CapabilitySet, FilesystemHandle

        opened: list[str] = []
        ctx = PluginContext(
            io=IOHandle(),
            fs=FilesystemHandle(_open_file_impl=lambda target: opened.append(str(target))),
            config_path=str(cfg_file),
            capabilities=CapabilitySet(gui_apps=True),
        )
        return ctx, opened

    def test_show_opens_the_named_file(self, tmp_path):
        """/cap.show adc opens cap/adc.csv, not the newest file; a bare stem is enough."""
        # Arrange
        from termapy.folder_ops import build_folder_subcommands

        cfg_file, cap_dir, _secret = _setup(tmp_path)
        (cap_dir / "newer.csv").write_text("4\n", encoding="utf-8")
        ctx, opened = self._show_ctx(cfg_file)
        show = build_folder_subcommands("cap")["show"].handler
        assert show is not None, "cap/ is showable"

        # Act
        result = show(ctx, "adc.csv")

        # Assert
        assert result.success, result.error
        assert [Path(target).name for target in opened] == ["adc.csv"], (
            "the file named, not the newest"
        )

    def test_show_refuses_a_name_outside_the_folder(self, tmp_path):
        """A traversal name fails and opens nothing: .show is as contained as .dump."""
        # Arrange
        from termapy.folder_ops import build_folder_subcommands

        cfg_file, _cap, _secret = _setup(tmp_path)
        ctx, opened = self._show_ctx(cfg_file)
        show = build_folder_subcommands("cap")["show"].handler
        assert show is not None, "cap/ is showable"

        # Act
        result = show(ctx, "../secret.txt")

        # Assert
        assert not result.success, "the name escapes cap/"
        assert opened == [], "nothing was opened"
