"""Private handlers for the /cfg.icon sub-command.

Filename is underscore-prefixed so the plugin loader skips this
module -- the actual sub-command is mounted in ``cfg.py`` as part
of ``/cfg``'s ``sub_commands`` dict.

``/cfg.icon`` creates a desktop/menu launcher for the currently
loaded cfg so non-CLI users can double-click their way to a
terminal session with the right cfg preloaded.

Per platform:

- **Linux**: write an XDG ``.desktop`` file into
  ``~/.local/share/applications/`` so it shows up in the
  application menu.  ``Terminal=true`` tells the DE to wrap the
  invocation in its preferred terminal; we don't have to guess.
- **macOS**: copy a small ``.app`` bundle template to
  ``~/Applications/<name>.app`` with the launcher script
  substituted to call ``osascript`` -> Terminal.app -> termapy.
  Looks like a real app in Finder and the Dock.
- **Windows**: invoke PowerShell to create a ``.lnk`` shortcut on
  the user's Desktop pointing at ``cmd /k termapy --cfg-dir <...>``.
  Stdlib-only (no ``pywin32`` dependency).

Templates live in ``src/termapy/templates/`` and are bundled into
the wheel by uv-build's default include-everything rule.  Token
substitutions happen in memory at install time; the templates
themselves are never executed directly.

No custom icon assets ship in v1.  Linux uses the system
``utilities-terminal`` icon, macOS falls back to the generic
``.app`` icon, Windows uses ``cmd.exe``'s icon.  Future
enhancement: ``cfg["icon_path"]`` for per-cfg artwork.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import termapy
from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# Templates ship next to the package; uv-build bundles them by default.
_TEMPLATES_DIR = Path(termapy.__file__).parent / "templates"

# Cached at module load so tests can monkeypatch _cfg_icon._PLATFORM
# without touching ``sys.platform`` globally -- pytest's own compat
# code reads sys.platform and breaks if the global value is wrong
# for the host (e.g. trying os.getuid() on a Windows test runner).
_PLATFORM = sys.platform


def _sanitize_filename(s: str) -> str:
    """Strip filesystem-unsafe characters from a config name.

    Used for Linux ``.desktop`` file IDs and Windows ``.lnk``
    filenames.  Conservative -- only keeps alnum, dot, dash,
    underscore.  Spaces become underscore (Linux file IDs don't
    like them; Windows can survive them but we normalize).
    """
    return "".join(c if c.isalnum() or c in "_-." else "_" for c in s)


def _ps_quote(s: str | Path) -> str:
    """Quote a string for a PowerShell single-quoted literal.

    PowerShell escape for ``'`` inside a single-quoted string is
    two single quotes.  Used by the Windows launcher path that
    builds a PowerShell COM-call script inline.
    """
    return "'" + str(s).replace("'", "''") + "'"


# ── Linux: write an XDG .desktop file ────────────────────────────────────────


def _linux_menu_path(stem: str) -> Path:
    """Path where ``/cfg.icon`` writes the Linux launcher."""
    return (
        Path.home() / ".local" / "share" / "applications"
        / f"termapy-{_sanitize_filename(stem)}.desktop"
    )


def _create_linux(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    """Write a ``.desktop`` file into the user's XDG menu directory.

    Returns ``(path, None)`` on success or ``(None, error_msg)``.
    """
    template_path = _TEMPLATES_DIR / "linux" / "termapy.desktop.template"
    if not template_path.is_file():
        return None, f"Template missing: {template_path}"
    icon_path = _TEMPLATES_DIR / "icons" / "termapy.png"
    content = (
        template_path.read_text(encoding="utf-8")
        .replace("__NAME__", name)
        .replace("__PYTHON__", sys.executable)
        .replace("__CFG_PATH__", str(cfg_path))
        .replace("__ICON_PATH__", str(icon_path))
    )

    target = _linux_menu_path(stem)
    if target.exists() and not force:
        return None, (
            f"Launcher already exists: {target.name}.  "
            f"Use --force to overwrite."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target, None


# ── macOS: copy + substitute an .app bundle ──────────────────────────────────


def _macos_bundle_path(name: str) -> Path:
    """Path where ``/cfg.icon`` writes the macOS .app bundle."""
    return Path.home() / "Applications" / f"{name}.app"


def _create_macos(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    """Build a per-cfg ``.app`` bundle under ``~/Applications/``."""
    template_dir = _TEMPLATES_DIR / "macos" / "Termapy.app"
    plist_template = template_dir / "Contents" / "Info.plist"
    launcher_template = (
        template_dir / "Contents" / "MacOS" / "launcher.template"
    )
    if not plist_template.is_file() or not launcher_template.is_file():
        return None, "macOS templates missing from package."

    target = _macos_bundle_path(name)
    if target.exists():
        if not force:
            return None, (
                f"Launcher already exists: {target.name}.  "
                f"Use --force to overwrite."
            )
        shutil.rmtree(target)

    contents = target / "Contents"
    (contents / "MacOS").mkdir(parents=True)

    # Copy the bundled .icns into Resources/.  Info.plist's
    # CFBundleIconFile=termapy resolves to Contents/Resources/termapy.icns
    # automatically -- no extra plist key needed beyond the static
    # template.
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    icns_src = _TEMPLATES_DIR / "icons" / "termapy.icns"
    if icns_src.is_file():
        shutil.copy(icns_src, resources / "termapy.icns")

    bundle_id = _sanitize_filename(stem.lower()).replace("_", "-")
    plist = (
        plist_template.read_text(encoding="utf-8")
        .replace("__NAME__", name)
        .replace("__BUNDLE_ID__", bundle_id)
    )
    (contents / "Info.plist").write_text(plist, encoding="utf-8")

    launcher = (
        launcher_template.read_text(encoding="utf-8")
        .replace("__NAME__", name)
        .replace("__PYTHON__", sys.executable)
        .replace("__CFG_PATH__", str(cfg_path))
    )
    launcher_path = contents / "MacOS" / "launcher"
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(0o755)
    return target, None


# ── Windows: PowerShell-generated .lnk on Desktop ────────────────────────────


_PS_RESOLVE_DESKTOP = (
    "[Environment]::GetFolderPath('Desktop')"
)


def _windows_desktop_path() -> Path:
    """Ask PowerShell for the user's Desktop folder (handles OneDrive)."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_RESOLVE_DESKTOP],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        # Fallback if PowerShell is missing or fails.  Less accurate
        # (no OneDrive redirection) but still usable on a stock install.
        return Path.home() / "Desktop"
    return Path(proc.stdout.strip())


def _windows_lnk_path(name: str) -> Path:
    """Path where ``/cfg.icon`` writes the Windows .lnk."""
    return _windows_desktop_path() / f"{_sanitize_filename(name)}.lnk"


def _create_windows(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    """Create a .lnk on the user's Desktop via PowerShell COM call."""
    filename = _sanitize_filename(name) + ".lnk"
    target_exe = r"C:\Windows\System32\cmd.exe"
    # /k = run command and keep the window open.  Sensible default
    # for "I'm not a CLI person" -- they see termapy exit cleanly
    # rather than the window vanishing mid-output.
    #
    # We invoke ``<sys.executable> -m termapy "<cfg>"`` instead of
    # bare ``termapy`` so the launcher is tied to the exact python +
    # termapy that created it.  Bare ``termapy`` resolves via PATH,
    # which can pick up an older globally-installed version and warn
    # about config keys the current dev tree understands.
    #
    # The outer "" pair on the /k command works around cmd.exe's
    # quote-stripping rule: when there are more than two quote chars,
    # cmd strips the first and last, leaving the inner quoted command.
    python_exe = sys.executable
    arguments = f'/k ""{python_exe}" -m termapy "{cfg_path}""'
    working_dir = str(cfg_path.parent)
    ico_path = _TEMPLATES_DIR / "icons" / "termapy.ico"

    overwrite_guard = (
        ""
        if force
        else (
            "if (Test-Path $path) { "
            "  Write-Error ('EXISTS:' + $path); "
            "  exit 1 "
            "};"
        )
    )

    ps = (
        "$ErrorActionPreference = 'Stop';"
        "$desktop = " + _PS_RESOLVE_DESKTOP + ";"
        f"$path = Join-Path $desktop {_ps_quote(filename)};"
        f"{overwrite_guard}"
        "$ws = New-Object -ComObject WScript.Shell;"
        "$lnk = $ws.CreateShortcut($path);"
        f"$lnk.TargetPath = {_ps_quote(target_exe)};"
        f"$lnk.Arguments = {_ps_quote(arguments)};"
        f"$lnk.WorkingDirectory = {_ps_quote(working_dir)};"
        f"$lnk.IconLocation = {_ps_quote(ico_path)};"
        "$lnk.Save();"
        "Write-Output $path"
    )

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"PowerShell invocation failed: {e}"

    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "EXISTS:" in err:
            return None, (
                f"Launcher already exists on Desktop: {filename}.  "
                f"Use --force to overwrite."
            )
        return None, err or "PowerShell shortcut creation failed."

    created = proc.stdout.strip()
    return Path(created) if created else None, None


# ── Dispatch ─────────────────────────────────────────────────────────────────


def _create(
    ctx: PluginContext,
    name: str,
    stem: str,
    cfg_path: Path,
    force: bool,
) -> CmdResult:
    plat = _PLATFORM
    if plat.startswith("linux"):
        path, err = _create_linux(name, stem, cfg_path, force)
    elif plat == "darwin":
        path, err = _create_macos(name, stem, cfg_path, force)
    elif plat == "win32":
        path, err = _create_windows(name, stem, cfg_path, force)
    else:
        return CmdResult.fail(msg=f"Unsupported platform: {plat}")

    if err:
        return CmdResult.fail(msg=err)
    if path is None:
        return CmdResult.fail(msg="Launcher creation returned no path.")
    ctx.io.result(f"Launcher created: {path}")
    return CmdResult.ok(value=str(path))


def _resolve_remove_path(name: str, stem: str) -> Path | None:
    """Return the launcher path for the current platform, or None."""
    plat = _PLATFORM
    if plat.startswith("linux"):
        return _linux_menu_path(stem)
    if plat == "darwin":
        return _macos_bundle_path(name)
    if plat == "win32":
        return _windows_lnk_path(name)
    return None


def _remove(ctx: PluginContext, name: str, stem: str) -> CmdResult:
    path = _resolve_remove_path(name, stem)
    if path is None:
        return CmdResult.fail(msg=f"Unsupported platform: {_PLATFORM}")
    if not path.exists():
        return CmdResult.fail(msg=f"Launcher not found: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    ctx.io.result(f"Launcher removed: {path}")
    return CmdResult.ok(value=str(path))


def _name_stem_path(ctx: PluginContext) -> tuple[str, str, Path] | None:
    """Resolve (display_name, filename_stem, absolute_cfg_path) from ctx.

    Returns None if no cfg is loaded -- callers should turn that into
    a ``CmdResult.fail(msg="No config loaded.")``.
    """
    if not ctx.config_path:
        return None
    cfg_path = Path(ctx.config_path).resolve()
    stem = cfg_path.stem
    title = ctx.cfg.get("title") or stem
    name = str(title).strip() or stem
    return name, stem, cfg_path


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon`` -- create a desktop launcher for the current cfg."""
    resolved = _name_stem_path(ctx)
    if resolved is None:
        return CmdResult.fail(msg="No config loaded.")
    name, stem, cfg_path = resolved
    return _create(ctx, name, stem, cfg_path, ctx.flag("--force"))


def _handler_remove(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon.remove`` -- delete the launcher for the current cfg."""
    resolved = _name_stem_path(ctx)
    if resolved is None:
        return CmdResult.fail(msg="No config loaded.")
    name, stem, _ = resolved
    return _remove(ctx, name, stem)


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon.list`` -- list every termapy launcher this OS can see."""
    return _list(ctx)


# ── List existing launchers ──────────────────────────────────────────────────


def _scan_linux() -> list[Path]:
    """Linux: termapy-*.desktop files under ~/.local/share/applications/."""
    apps = Path.home() / ".local" / "share" / "applications"
    if not apps.is_dir():
        return []
    return sorted(apps.glob("termapy-*.desktop"))


def _scan_macos() -> list[Path]:
    """macOS: .app bundles under ~/Applications/ whose plist carries our bundle ID."""
    apps = Path.home() / "Applications"
    if not apps.is_dir():
        return []
    found: list[Path] = []
    for app in sorted(apps.glob("*.app")):
        plist = app / "Contents" / "Info.plist"
        if not plist.is_file():
            continue
        try:
            text = plist.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Bundle-ID prefix is the unambiguous marker -- every termapy
        # launcher carries net.termapy.<slug> in CFBundleIdentifier.
        if "net.termapy." in text:
            found.append(app)
    return found


def _scan_windows() -> list[Path]:
    """Windows: .lnk files on the Desktop whose Arguments mention termapy.

    Uses a single PowerShell call to enumerate every .lnk and read its
    Arguments via the WScript.Shell COM API.  Output is tab-separated
    ``<lnk-path>\\t<arguments>`` per line so we can filter in Python.
    """
    desktop = _windows_desktop_path()
    if not desktop.is_dir():
        return []
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"Get-ChildItem {_ps_quote(desktop)} -Filter *.lnk | ForEach-Object {{"
        "  $s = $ws.CreateShortcut($_.FullName);"
        "  Write-Output (\"{0}`t{1}\" -f $_.FullName, $s.Arguments)"
        "}"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    found: list[Path] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        path_str, args = line.split("\t", 1)
        # Match "termapy" in arguments -- catches both ``-m termapy``
        # (our launchers) and bare ``termapy ...`` if anyone shipped
        # such a shortcut.  Case-insensitive for safety.
        if "termapy" in args.lower():
            found.append(Path(path_str))
    return found


def _list(ctx: PluginContext) -> CmdResult:
    """Print every termapy launcher we can find in the platform's install dir."""
    plat = _PLATFORM
    if plat.startswith("linux"):
        found = _scan_linux()
        where = "~/.local/share/applications/"
    elif plat == "darwin":
        found = _scan_macos()
        where = "~/Applications/"
    elif plat == "win32":
        found = _scan_windows()
        where = "Desktop"
    else:
        return CmdResult.fail(msg=f"Unsupported platform: {plat}")

    if not found:
        ctx.io.result(f"No termapy launchers found in {where}.")
        return CmdResult.ok(value="")
    for path in found:
        ctx.io.output(f"  {path}")
    return CmdResult.ok(value="\n".join(str(p) for p in found))


_LONG_HELP = """\
Create a desktop / menu launcher for the currently loaded cfg so
non-CLI users can double-click to open a terminal with termapy
running their device's config.

Usage:
  /cfg.icon              Create the launcher.
  /cfg.icon --force      Overwrite an existing launcher.
  /cfg.icon.remove       Delete the launcher for the current cfg.
  /cfg.icon.list         List every termapy launcher this platform sees
                         (doesn't need a cfg loaded).

Per platform:

  Linux    ~/.local/share/applications/termapy-<name>.desktop
           (visible in the application menu; Terminal=true so the
           DE picks its preferred terminal automatically)
  macOS    ~/Applications/<title>.app
           (a small bundle that opens Terminal.app and runs termapy
           with the cfg dir preloaded)
  Windows  Desktop\\<title>.lnk
           (generated via PowerShell COM call; target is
           ``cmd /k "<python>" -m termapy "<cfg file>"``)

The launcher embeds:
  - the absolute path to the python that's currently running termapy
    (so it stays tied to this install, not whichever bare ``termapy``
    happens to be first on PATH later)
  - the absolute path to this .cfg file

If you move/rename the cfg or change termapy installs, rerun
/cfg.icon to refresh.

All three platforms get the same custom termapy icon (phosphor-green
``>`` prompt + square-wave pulse train on a near-black frame),
rendered from a single SVG source at install time.  See
``src/termapy/templates/icons/termapy.svg``.
"""


_FLAGS = {
    "--force": "Overwrite an existing launcher.",
}


__all__ = [
    "_FLAGS",
    "_LONG_HELP",
    "_handler",
    "_handler_list",
    "_handler_remove",
]
