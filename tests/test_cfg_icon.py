"""Tests for /cfg.icon -- per-cfg desktop launcher creation.

Each platform writes a different artefact (Linux .desktop file,
macOS .app bundle, Windows .lnk).  Tests monkeypatch
``sys.platform`` and redirect ``Path.home()`` into ``tmp_path`` so
the test never touches the developer's real home directory.  The
Windows path mocks ``subprocess.run`` for the PowerShell COM
invocation; we verify the shape of the script that would have
been run, not the .lnk on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.builtins.commands import _cfg_icon
from termapy.repl import ReplEngine


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    """Build a minimal engine + redirect Path.home() into tmp_path."""
    # Redirect the user home so the test never writes to a real
    # ~/Applications, ~/Desktop, or ~/.local.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # Real-ish cfg on disk so cfg_path / cfg_dir resolve correctly.
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "line_ending": "\r",
        "title": "Demo Device",
    }
    cfg_dir = tmp_path / "termapy_cfg" / "demo"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "demo.cfg"
    cfg_path.write_text(json.dumps(cfg))

    output: list = []
    eng = ReplEngine(
        cfg, str(cfg_path), lambda t, c=None: output.append((t, c)),
    )
    eng.ctx.config_path = str(cfg_path)
    eng.ctx.cfg = cfg
    # /cfg.icon needs gui_apps (a desktop-launcher feature); grant it so
    # these feature tests run.  A headless MCP host lacks it -- that gating
    # is asserted in test_fs_sandbox.py.
    from termapy.plugins import CapabilitySet
    eng.ctx.capabilities = CapabilitySet(interactive=True, gui_apps=True)
    eng.ctx.sync_capabilities()
    flags = eng.ctx.ns("flags")
    flags["output_level"] = "verbose"
    return eng, cfg_dir, fake_home, output


def _set_flag(ctx, name: str) -> None:
    """Add a flag to ctx.active_flags so ctx.flag(name) returns True."""
    if not hasattr(ctx, "active_flags") or ctx.active_flags is None:
        ctx.active_flags = set()
    ctx.active_flags.add(name)


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestSanitizeFilename:
    def test_alnum_passes_through(self):
        # Arrange / Act / Assert
        assert _cfg_icon._sanitize_filename("MyDevice123") == "MyDevice123", (
            "alnum survives untouched"
        )

    def test_spaces_become_underscores(self):
        # Arrange / Act / Assert
        assert _cfg_icon._sanitize_filename("Demo Device") == "Demo_Device", (
            "spaces -> underscores (filesystem-safe across all 3 OSes)"
        )

    def test_special_chars_become_underscores(self):
        # Arrange / Act
        actual = _cfg_icon._sanitize_filename("foo/bar:baz*qux")

        # Assert
        assert actual == "foo_bar_baz_qux", (
            "path separators and shell metacharacters replaced"
        )

    def test_dots_and_dashes_kept(self):
        # Arrange / Act
        actual = _cfg_icon._sanitize_filename("v1.2-rc3")

        # Assert -- dots in version-like names are common and safe.
        assert actual == "v1.2-rc3", "dots and dashes preserved"


class TestPsQuote:
    def test_simple_string_wrapped_in_single_quotes(self):
        assert _cfg_icon._ps_quote("hello") == "'hello'", (
            "wraps in single quotes for PowerShell literal"
        )

    def test_single_quote_doubled(self):
        # Arrange / Act -- PowerShell escapes ' as '' inside a
        # single-quoted string.  Critical for paths like
        # ``C:\Users\Bill's PC\Desktop``.
        actual = _cfg_icon._ps_quote("it's a test")

        # Assert
        assert actual == "'it''s a test'", "embedded ' is doubled"

    def test_accepts_path_object(self):
        # Arrange / Act -- callers pass Path objects from
        # Path.home(); the quoter must str() them.
        actual = _cfg_icon._ps_quote(Path("C:/foo/bar"))

        # Assert
        assert "foo" in actual and actual.startswith("'"), (
            "Path stringified and wrapped"
        )


# ── Linux: .desktop creation ─────────────────────────────────────────────────


class TestLinuxCreate:
    """Linux path writes an XDG .desktop file into ~/.local/share/applications/."""

    @pytest.fixture(autouse=True)
    def _platform_linux(self, monkeypatch):
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")

    def test_writes_desktop_file_into_xdg_apps_dir(self, cfg_env):
        # Arrange
        eng, cfg_dir, fake_home, _ = cfg_env

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert
        assert result.success, f"create succeeded, got: {result.error}"
        expected_path = (
            fake_home / ".local" / "share" / "applications"
            / "termapy-demo.desktop"
        )
        assert expected_path.is_file(), (
            f"launcher landed in XDG menu dir: {expected_path}"
        )
        assert result.value == str(expected_path), (
            "CmdResult.value is the created path"
        )

    def test_content_substitutes_name_and_cfg_path(self, cfg_env):
        # Arrange
        eng, cfg_dir, fake_home, _ = cfg_env

        # Act
        eng.dispatch("cfg.icon")

        # Assert -- the .desktop file embeds the cfg title and the
        # absolute .cfg file path, both substituted from the template.
        path = (
            fake_home / ".local" / "share" / "applications"
            / "termapy-demo.desktop"
        )
        content = path.read_text(encoding="utf-8")
        cfg_file = (cfg_dir / "demo.cfg").resolve()
        import sys as _sys
        assert "Name=Demo Device" in content, (
            "Name= field carries the cfg title"
        )
        assert f'"{_sys.executable}" -m termapy "{cfg_file}"' in content, (
            "Exec= line invokes <python> -m termapy <cfg-path>"
        )
        assert "Terminal=true" in content, (
            "Terminal=true so the DE picks its preferred terminal"
        )
        for placeholder in (
            "__NAME__", "__PYTHON__", "__CFG_PATH__", "__ICON_PATH__",
        ):
            assert placeholder not in content, (
                f"no leftover {placeholder} placeholder"
            )
        # Icon= must point at the bundled termapy.png and the file
        # must actually exist on disk -- sanity that the binary is
        # bundled in the wheel install.
        assert "Icon=" in content and "termapy.png" in content, (
            "Icon= line points at the bundled termapy.png"
        )
        icon_line = next(
            line for line in content.splitlines() if line.startswith("Icon=")
        )
        icon_file = Path(icon_line.removeprefix("Icon="))
        assert icon_file.is_file(), (
            f"bundled icon present on disk: {icon_file}"
        )

    def test_refuses_if_exists_without_force(self, cfg_env):
        # Arrange -- first create succeeds; second errors.
        eng, _, _, _ = cfg_env
        eng.dispatch("cfg.icon")

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert
        assert not result.success, "refuse-if-exists"
        assert "exists" in result.error.lower(), (
            "error names the failure mode"
        )

    def test_force_overwrites_existing(self, cfg_env):
        # Arrange
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        path = (
            fake_home / ".local" / "share" / "applications"
            / "termapy-demo.desktop"
        )
        # Mutate the file so we can verify rewrite.
        path.write_text("STALE\n", encoding="utf-8")

        # Act
        result = eng.dispatch("cfg.icon --force")

        # Assert
        assert result.success, "second create with --force succeeds"
        content = path.read_text(encoding="utf-8")
        assert "Name=Demo Device" in content, (
            "fresh template content written, stale text gone"
        )

    def test_remove_deletes_launcher(self, cfg_env):
        # Arrange
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        path = (
            fake_home / ".local" / "share" / "applications"
            / "termapy-demo.desktop"
        )
        assert path.is_file(), "fixture sanity: file exists"

        # Act
        result = eng.dispatch("cfg.icon.remove")

        # Assert
        assert result.success, "remove succeeded"
        assert not path.exists(), "file deleted"

    def test_remove_without_launcher_fails(self, cfg_env):
        # Arrange / Act
        eng, _, _, _ = cfg_env
        result = eng.dispatch("cfg.icon.remove")

        # Assert
        assert not result.success, "remove with no launcher fails"
        assert "not found" in result.error.lower(), (
            "error explains why"
        )


# ── macOS: .app bundle ───────────────────────────────────────────────────────


class TestMacosCreate:
    """macOS path builds an .app bundle in ~/Applications/."""

    @pytest.fixture(autouse=True)
    def _platform_darwin(self, monkeypatch):
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "darwin")

    def test_builds_app_bundle_under_applications(self, cfg_env):
        # Arrange
        eng, cfg_dir, fake_home, _ = cfg_env

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert -- the bundle is a directory tree with Info.plist
        # and a chmod-+x launcher script inside.
        assert result.success, f"create succeeded, got: {result.error}"
        bundle = fake_home / "Applications" / "Demo Device.app"
        assert bundle.is_dir(), "bundle directory created"
        plist = bundle / "Contents" / "Info.plist"
        launcher = bundle / "Contents" / "MacOS" / "launcher"
        icns = bundle / "Contents" / "Resources" / "termapy.icns"
        assert plist.is_file(), "Info.plist written"
        assert launcher.is_file(), "launcher script written"
        # The bundled .icns must land in Resources/ so Finder/Dock
        # render the custom icon (Info.plist's CFBundleIconFile points
        # to "termapy" -> resolves to Resources/termapy.icns).
        assert icns.is_file(), (
            "bundled termapy.icns copied into Contents/Resources/"
        )
        plist_text = plist.read_text(encoding="utf-8")
        assert "CFBundleIconFile" in plist_text, (
            "plist declares the icon file"
        )

    def test_plist_and_launcher_substitute_placeholders(self, cfg_env):
        # Arrange
        eng, cfg_dir, fake_home, _ = cfg_env

        # Act
        eng.dispatch("cfg.icon")

        # Assert
        bundle = fake_home / "Applications" / "Demo Device.app"
        plist_content = (
            bundle / "Contents" / "Info.plist"
        ).read_text(encoding="utf-8")
        launcher_content = (
            bundle / "Contents" / "MacOS" / "launcher"
        ).read_text(encoding="utf-8")

        assert "<string>Demo Device</string>" in plist_content, (
            "CFBundleName carries the cfg title"
        )
        assert "__NAME__" not in plist_content, (
            "plist placeholder filled"
        )
        for placeholder in ("__NAME__", "__PYTHON__", "__CFG_PATH__"):
            assert placeholder not in launcher_content, (
                f"no leftover {placeholder} in launcher"
            )
        cfg_file = (cfg_dir / "demo.cfg").resolve()
        import sys as _sys
        assert str(cfg_file) in launcher_content, (
            "launcher embeds the absolute .cfg file path"
        )
        assert f"'{_sys.executable}' -m termapy '{cfg_file}'" in launcher_content, (
            "launcher invokes <python> -m termapy '<cfg>'"
        )

    def test_refuses_if_bundle_exists_without_force(self, cfg_env):
        # Arrange
        eng, _, _, _ = cfg_env
        eng.dispatch("cfg.icon")

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert
        assert not result.success, "second create fails"
        assert "exists" in result.error.lower(), "error names the issue"

    def test_force_rewrites_bundle(self, cfg_env):
        # Arrange
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        # Drop a stray file inside the bundle to prove rmtree happened.
        stray = (
            fake_home / "Applications" / "Demo Device.app"
            / "Contents" / "stale_marker"
        )
        stray.write_text("stale")

        # Act
        result = eng.dispatch("cfg.icon --force")

        # Assert
        assert result.success, "force succeeded"
        assert not stray.exists(), "stale content removed (rmtree)"

    def test_remove_rmtrees_bundle(self, cfg_env):
        # Arrange
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        bundle = fake_home / "Applications" / "Demo Device.app"
        assert bundle.is_dir(), "fixture sanity"

        # Act
        result = eng.dispatch("cfg.icon.remove")

        # Assert
        assert result.success, "remove succeeded"
        assert not bundle.exists(), "whole bundle directory removed"


# ── Windows: .lnk via PowerShell ─────────────────────────────────────────────


class _FakeProc:
    """Minimal CompletedProcess stand-in for subprocess.run mocking."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestWindowsCreate:
    """Windows path generates a .lnk via PowerShell COM call.

    The test mocks ``subprocess.run`` so PowerShell is never
    actually launched.  We capture the script string and verify
    it carries the right WScript.Shell COM call shape.
    """

    @pytest.fixture(autouse=True)
    def _platform_win32(self, monkeypatch):
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "win32")

    def test_invokes_powershell_with_expected_shortcut_call(
        self, cfg_env, monkeypatch,
    ):
        # Arrange -- capture every subprocess.run call so we can
        # inspect the PowerShell script the implementation built.
        calls: list[list[str]] = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            # First call resolves Desktop; second creates the .lnk.
            if cmd[-1] == _cfg_icon._PS_RESOLVE_DESKTOP:
                return _FakeProc(0, stdout="C:/Users/test/Desktop\n")
            # Shortcut creation script -- success path.
            return _FakeProc(
                0, stdout="C:/Users/test/Desktop/Demo_Device.lnk\n",
            )

        monkeypatch.setattr(_cfg_icon.subprocess, "run", _fake_run)

        # Act
        eng, cfg_dir, _, _ = cfg_env
        result = eng.dispatch("cfg.icon")

        # Assert
        assert result.success, f"create succeeded, got: {result.error}"
        assert any(
            "CreateShortcut" in (c[-1] if isinstance(c, list) else "")
            for c in calls
        ), "PowerShell COM script was invoked"
        # The script must reference cmd.exe, embed the cfg dir, and
        # set WorkingDirectory.
        scripts = [c[-1] for c in calls if isinstance(c, list)]
        shortcut_script = next(
            s for s in scripts if "CreateShortcut" in s
        )
        assert "cmd.exe" in shortcut_script, "target is cmd.exe"
        cfg_file = (cfg_dir / "demo.cfg").resolve()
        assert str(cfg_file) in shortcut_script, (
            "cfg file path embedded in the shortcut arguments"
        )
        import sys as _sys
        assert _sys.executable in shortcut_script, (
            "current python.exe embedded so launcher uses this install"
        )
        assert "-m termapy" in shortcut_script, (
            "launcher invokes python -m termapy (not bare termapy)"
        )
        assert "WorkingDirectory" in shortcut_script, (
            "shortcut sets WorkingDirectory"
        )
        # IconLocation must point at the bundled .ico so the Desktop
        # shortcut renders the custom termapy artwork instead of
        # inheriting cmd.exe's icon.
        assert "IconLocation" in shortcut_script, (
            "shortcut sets IconLocation"
        )
        assert "termapy.ico" in shortcut_script, (
            "IconLocation points at bundled termapy.ico"
        )
        # CmdResult.ok stores str(Path(value).resolve()).  On the real
        # Windows target that leaves the absolute "C:\\..." path unchanged,
        # but on a POSIX CI host "C:/..." is a *relative* path, so resolve()
        # prepends the cwd.  Assert the meaningful tail (drive + full path),
        # separator-normalized, rather than the host-dependent prefix.
        got = str(result.value).replace("\\", "/")
        assert got.endswith("C:/Users/test/Desktop/Demo_Device.lnk"), (
            f"CmdResult.value is the resolved Desktop path, got: {result.value!r}"
        )

    def test_refuses_if_lnk_exists_without_force(
        self, cfg_env, monkeypatch,
    ):
        # Arrange -- simulate PowerShell reporting EXISTS:<path>.
        def _fake_run(cmd, **kwargs):
            if cmd[-1] == _cfg_icon._PS_RESOLVE_DESKTOP:
                return _FakeProc(0, stdout="C:/Desktop\n")
            return _FakeProc(
                1, stderr="EXISTS:C:/Desktop/Demo_Device.lnk",
            )

        monkeypatch.setattr(_cfg_icon.subprocess, "run", _fake_run)

        # Act
        eng, _, _, _ = cfg_env
        result = eng.dispatch("cfg.icon")

        # Assert
        assert not result.success, "EXISTS: marker -> CmdResult.fail"
        assert "exists" in result.error.lower(), "error names the issue"

    def test_force_omits_existence_guard(self, cfg_env, monkeypatch):
        # Arrange -- record the shortcut-creation script so we can
        # check that the "Test-Path" guard isn't present when --force.
        scripts: list[str] = []

        def _fake_run(cmd, **kwargs):
            if cmd[-1] == _cfg_icon._PS_RESOLVE_DESKTOP:
                return _FakeProc(0, stdout="C:/Desktop\n")
            scripts.append(cmd[-1])
            return _FakeProc(
                0, stdout="C:/Desktop/Demo_Device.lnk\n",
            )

        monkeypatch.setattr(_cfg_icon.subprocess, "run", _fake_run)

        # Act
        eng, _, _, _ = cfg_env
        result = eng.dispatch("cfg.icon --force")

        # Assert
        assert result.success, "force path succeeded"
        assert scripts, "shortcut script was invoked"
        assert "Test-Path" not in scripts[0], (
            "--force drops the existence guard"
        )


# ── Dispatch + error paths ───────────────────────────────────────────────────


class TestList:
    """``/cfg.icon.list`` scans the platform's launcher dir."""

    def test_linux_lists_termapy_desktop_files(self, cfg_env, monkeypatch):
        # Arrange -- create two launchers via the create path, then
        # plant a non-termapy .desktop that must be ignored.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        apps = fake_home / ".local" / "share" / "applications"
        (apps / "firefox.desktop").write_text(
            "[Desktop Entry]\nName=Firefox\n", encoding="utf-8",
        )

        # Act
        result = eng.dispatch("cfg.icon.list")

        # Assert -- exactly the one termapy launcher; firefox excluded.
        assert result.success, "list succeeded"
        lines = result.value.splitlines()
        assert len(lines) == 1, (
            f"only termapy-* matches, got {lines!r}"
        )
        assert "termapy-demo.desktop" in lines[0], (
            "list contains the created launcher"
        )
        assert "firefox" not in result.value.lower(), (
            "non-termapy .desktop filtered out"
        )

    def test_macos_lists_bundles_by_bundle_id(self, cfg_env, monkeypatch):
        # Arrange -- one termapy .app + one foreign .app without our
        # bundle ID marker.  Scanner must keep ours, drop the other.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "darwin")
        eng, _, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        # Plant a foreign .app with a non-termapy bundle id.
        other = fake_home / "Applications" / "Other.app" / "Contents"
        other.mkdir(parents=True)
        (other / "Info.plist").write_text(
            "<plist><dict><key>CFBundleIdentifier</key>"
            "<string>com.other.app</string></dict></plist>",
            encoding="utf-8",
        )

        # Act
        result = eng.dispatch("cfg.icon.list")

        # Assert
        assert result.success, "list succeeded"
        lines = result.value.splitlines()
        assert len(lines) == 1, (
            f"only the termapy bundle matches, got {lines!r}"
        )
        assert "Demo Device.app" in lines[0], (
            "list contains the termapy bundle"
        )
        assert "Other.app" not in result.value, (
            "non-termapy bundle filtered out"
        )

    def test_windows_filters_by_args_containing_termapy(
        self, cfg_env, monkeypatch,
    ):
        # Arrange -- mock PowerShell to return two .lnks: one ours,
        # one with no termapy reference.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "win32")

        def _fake_run(cmd, **kwargs):
            script = cmd[-1] if isinstance(cmd, list) else ""
            if script == _cfg_icon._PS_RESOLVE_DESKTOP:
                return _FakeProc(0, stdout="C:/Desktop\n")
            # Enumerate-shortcuts script.  Tab-separated path + args.
            return _FakeProc(
                0,
                stdout=(
                    "C:/Desktop/Demo_Device.lnk\t"
                    '/k ""C:/py.exe" -m termapy "C:/demo.cfg""\n'
                    "C:/Desktop/notepad.lnk\t\n"
                ),
            )

        monkeypatch.setattr(_cfg_icon.subprocess, "run", _fake_run)

        # Need is_dir() to return True so the scanner doesn't bail
        # early.  _windows_desktop_path is mocked through subprocess
        # above so this never touches the disk.
        monkeypatch.setattr(
            _cfg_icon.Path, "is_dir", lambda self: True,
        )

        # Act
        eng, _, _, _ = cfg_env
        result = eng.dispatch("cfg.icon.list")

        # Assert
        assert result.success, "list succeeded"
        lines = result.value.splitlines()
        assert len(lines) == 1, (
            f"only the termapy .lnk matches, got {lines!r}"
        )
        assert "Demo_Device.lnk" in lines[0], (
            "list contains the termapy launcher"
        )
        assert "notepad" not in result.value.lower(), (
            "non-termapy .lnk filtered out"
        )

    def test_empty_when_none_found(self, cfg_env, monkeypatch):
        # Arrange -- linux but no .desktop files created.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, _, _, output = cfg_env

        # Act
        result = eng.dispatch("cfg.icon.list")

        # Assert
        assert result.success, "list returns ok even when empty"
        assert result.value == "", "empty value when no launchers"
        joined = "".join(str(t) for t in output)
        assert "no termapy launchers" in joined.lower(), (
            "user-visible 'none found' message printed"
        )

    def test_list_works_without_loaded_cfg(self, cfg_env, monkeypatch):
        # Arrange -- --list is a "what's installed?" query and must
        # not require a loaded cfg.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, _, _, _ = cfg_env
        eng.ctx.config_path = ""

        # Act
        result = eng.dispatch("cfg.icon.list")

        # Assert
        assert result.success, (
            "--list succeeds with no cfg loaded (unlike create/remove)"
        )


class TestFindLauncherForCfg:
    """``find_launcher_for_cfg`` finds the launcher embedding a given cfg path.

    Used by the cfg-delete cleanup: when a user deletes a cfg, the
    app calls this to locate the matching desktop icon and removes
    it so no orphan launchers remain.
    """

    def test_linux_finds_desktop_file_referencing_cfg(self, cfg_env, monkeypatch):
        # Arrange -- create the launcher via the normal create path
        # so its embedded Exec= line carries the cfg path verbatim.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, cfg_dir, _, _ = cfg_env
        eng.dispatch("cfg.icon")
        cfg_file = (cfg_dir / "demo.cfg").resolve()

        # Act
        found = _cfg_icon.find_launcher_for_cfg(cfg_file)

        # Assert
        assert found is not None, "find returns the matching launcher"
        assert found.name == "termapy-demo.desktop", (
            "found path matches the created .desktop file"
        )

    def test_linux_returns_none_when_no_launcher_matches(
        self, cfg_env, monkeypatch,
    ):
        # Arrange -- create a launcher for one cfg, search for another.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, _, _, _ = cfg_env
        eng.dispatch("cfg.icon")

        # Act -- search for a cfg that no launcher references.
        found = _cfg_icon.find_launcher_for_cfg(
            Path("/nonexistent/other_cfg.cfg"),
        )

        # Assert
        assert found is None, "no launcher references the unrelated cfg"

    def test_macos_finds_bundle_referencing_cfg(self, cfg_env, monkeypatch):
        # Arrange
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "darwin")
        eng, cfg_dir, fake_home, _ = cfg_env
        eng.dispatch("cfg.icon")
        cfg_file = (cfg_dir / "demo.cfg").resolve()

        # Act
        found = _cfg_icon.find_launcher_for_cfg(cfg_file)

        # Assert
        assert found is not None, "find returns the bundle"
        assert found == fake_home / "Applications" / "Demo Device.app", (
            "found path is the bundle dir"
        )

    def test_windows_finds_lnk_by_args_substring(self, cfg_env, monkeypatch):
        # Arrange -- mock PowerShell to enumerate one .lnk whose
        # Arguments embed the cfg path we're searching for.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "win32")
        cfg_file = Path("C:/path/to/demo.cfg")

        def _fake_run(cmd, **kwargs):
            script = cmd[-1] if isinstance(cmd, list) else ""
            if script == _cfg_icon._PS_RESOLVE_DESKTOP:
                return _FakeProc(0, stdout="C:/Desktop\n")
            return _FakeProc(
                0,
                stdout=(
                    "C:/Desktop/Demo_Device.lnk\t"
                    f'/k ""C:/py.exe" -m termapy "{cfg_file}""\n'
                ),
            )

        monkeypatch.setattr(_cfg_icon.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            _cfg_icon.Path, "is_dir", lambda self: True,
        )

        # Act
        found = _cfg_icon.find_launcher_for_cfg(cfg_file)

        # Assert
        assert found is not None, "find matched on substring of cfg_file"
        assert found == Path("C:/Desktop/Demo_Device.lnk"), (
            "found the right .lnk path"
        )


class TestRemoveLauncherAt:
    """``remove_launcher_at`` deletes a launcher file or .app bundle."""

    def test_removes_a_file(self, tmp_path):
        # Arrange -- a fake .desktop file (any file is fine).
        target = tmp_path / "termapy-demo.desktop"
        target.write_text("dummy", encoding="utf-8")

        # Act
        _cfg_icon.remove_launcher_at(target)

        # Assert
        assert not target.exists(), "file deleted"

    def test_removes_a_bundle_directory(self, tmp_path):
        # Arrange -- a fake .app bundle (just a directory).
        bundle = tmp_path / "Demo.app"
        (bundle / "Contents").mkdir(parents=True)
        (bundle / "Contents" / "Info.plist").write_text("x", encoding="utf-8")

        # Act
        _cfg_icon.remove_launcher_at(bundle)

        # Assert
        assert not bundle.exists(), "whole bundle dir removed"

    def test_missing_path_is_a_noop(self, tmp_path):
        # Arrange / Act -- silent on missing.
        _cfg_icon.remove_launcher_at(tmp_path / "never_existed.lnk")
        # Assert -- no exception is the assertion.


class TestDispatch:
    def test_fails_when_no_cfg_loaded(self, cfg_env, monkeypatch):
        # Arrange -- a bare engine with no config_path.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "linux")
        eng, _, _, _ = cfg_env
        eng.ctx.config_path = ""

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert
        assert not result.success, "no cfg -> fail"
        assert "no config" in result.error.lower(), (
            "error names the precondition"
        )

    def test_unsupported_platform_fails_clean(self, cfg_env, monkeypatch):
        # Arrange -- exotic platform (e.g. aix, freebsd).  Shouldn't
        # crash; should fail with a clear message.
        monkeypatch.setattr(_cfg_icon, "_PLATFORM", "haiku")
        eng, _, _, _ = cfg_env

        # Act
        result = eng.dispatch("cfg.icon")

        # Assert
        assert not result.success, "unsupported platform -> fail"
        assert "unsupported" in result.error.lower(), (
            "error names the platform"
        )
