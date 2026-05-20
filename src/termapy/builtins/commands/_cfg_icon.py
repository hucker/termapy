"""Private handlers for the /cfg.icon sub-command.

Filename is underscore-prefixed so the plugin loader skips this
module; the actual sub-commands are mounted in ``cfg.py`` under
``/cfg``'s ``sub_commands`` dict.

``/cfg.icon`` creates a desktop / menu launcher for the currently
loaded cfg so non-CLI users can double-click into a terminal with
the right cfg preloaded.  Per platform:

- **Linux**: XDG ``.desktop`` file in ``~/.local/share/applications/``;
  ``Terminal=true`` lets the DE pick the user's preferred terminal.
- **macOS**: ``.app`` bundle in ``~/Applications/`` whose launcher
  shells out via ``osascript`` to Terminal.app.
- **Windows**: ``.lnk`` shortcut on the Desktop pointing at
  ``cmd /k "<python>" -m termapy "<cfg>"`` via the WScript.Shell
  COM API (stdlib subprocess + PowerShell, no pywin32).

Templates and rasterized icons ship under ``src/termapy/templates/``
and are bundled into the wheel by uv-build's default rule.  Token
substitutions happen in memory at install time.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import termapy
from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


_TEMPLATES_DIR = Path(termapy.__file__).parent / "templates"

# Cached at module load so tests can monkeypatch _cfg_icon._PLATFORM
# without mutating sys.platform globally (pytest's own compat code
# reads sys.platform and breaks if it's wrong for the host).
_PLATFORM = sys.platform


def _sanitize_filename(s: str) -> str:
    """Conservative: keep alnum/dot/dash/underscore; everything else to ``_``."""
    return "".join(c if c.isalnum() or c in "_-." else "_" for c in s)


def _ps_quote(s: str | Path) -> str:
    """Quote a value as a PowerShell single-quoted literal ('' escapes ')."""
    return "'" + str(s).replace("'", "''") + "'"


def _render_template(path: Path, **subs: str) -> str:
    """Read a template file and replace ``__TOKEN__`` markers with substitutions."""
    text = path.read_text(encoding="utf-8")
    for key, val in subs.items():
        text = text.replace(f"__{key}__", val)
    return text


def _already_exists(name: str, *, on_desktop: bool = False) -> str:
    """Standard 'launcher already exists' error message."""
    where = " on Desktop" if on_desktop else ""
    return f"Launcher already exists{where}: {name}.  Use --force to overwrite."


# ── Per-platform path resolvers ──────────────────────────────────────────────


def _linux_menu_path(stem: str) -> Path:
    return (
        Path.home() / ".local" / "share" / "applications"
        / f"termapy-{_sanitize_filename(stem)}.desktop"
    )


def _macos_bundle_path(name: str) -> Path:
    return Path.home() / "Applications" / f"{name}.app"


_PS_RESOLVE_DESKTOP = "[Environment]::GetFolderPath('Desktop')"


def _windows_desktop_path() -> Path:
    """Ask PowerShell for the user's Desktop folder (handles OneDrive)."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_RESOLVE_DESKTOP],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return Path.home() / "Desktop"
    return Path(proc.stdout.strip())


def _windows_lnk_path(name: str) -> Path:
    return _windows_desktop_path() / f"{_sanitize_filename(name)}.lnk"


# ── Platform dispatch helper ─────────────────────────────────────────────────


_T = TypeVar("_T")


def _by_platform(linux: _T, darwin: _T, win32: _T) -> _T | None:
    """Pick the linux/darwin/win32 value for the current platform, or None."""
    plat = _PLATFORM
    if plat.startswith("linux"):
        return linux
    if plat == "darwin":
        return darwin
    if plat == "win32":
        return win32
    return None


# ── Linux create ─────────────────────────────────────────────────────────────


def _create_linux(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    template_path = _TEMPLATES_DIR / "linux" / "termapy.desktop.template"
    if not template_path.is_file():
        return None, f"Template missing: {template_path}"
    target = _linux_menu_path(stem)
    if target.exists() and not force:
        return None, _already_exists(target.name)
    content = _render_template(
        template_path,
        NAME=name,
        PYTHON=sys.executable,
        CFG_PATH=str(cfg_path),
        ICON_PATH=str(_TEMPLATES_DIR / "icons" / "termapy.png"),
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target, None


# ── macOS create ─────────────────────────────────────────────────────────────


def _create_macos(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    template_dir = _TEMPLATES_DIR / "macos" / "Termapy.app"
    plist_template = template_dir / "Contents" / "Info.plist"
    launcher_template = template_dir / "Contents" / "MacOS" / "launcher.template"
    if not plist_template.is_file() or not launcher_template.is_file():
        return None, "macOS templates missing from package."

    target = _macos_bundle_path(name)
    if target.exists():
        if not force:
            return None, _already_exists(target.name)
        shutil.rmtree(target)

    contents = target / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    icns_src = _TEMPLATES_DIR / "icons" / "termapy.icns"
    if icns_src.is_file():
        shutil.copy(icns_src, resources / "termapy.icns")

    bundle_id = _sanitize_filename(stem.lower()).replace("_", "-")
    (contents / "Info.plist").write_text(
        _render_template(plist_template, NAME=name, BUNDLE_ID=bundle_id),
        encoding="utf-8",
    )
    launcher_path = contents / "MacOS" / "launcher"
    launcher_path.write_text(
        _render_template(
            launcher_template,
            NAME=name, PYTHON=sys.executable, CFG_PATH=str(cfg_path),
        ),
        encoding="utf-8",
    )
    launcher_path.chmod(0o755)
    return target, None


# ── Windows create ───────────────────────────────────────────────────────────


def _create_windows(
    name: str, stem: str, cfg_path: Path, force: bool,
) -> tuple[Path | None, str | None]:
    """Create a Desktop .lnk via PowerShell + WScript.Shell COM."""
    filename = _sanitize_filename(name) + ".lnk"
    # cmd /k = run + keep window open after termapy exits.
    # Outer "" wraps the whole inner command so cmd's quote-stripping
    # rule preserves the inner quotes around python_exe and cfg_path.
    # We use sys.executable + -m termapy (not bare ``termapy``) so the
    # launcher pins the exact install that ran /cfg.icon, instead of
    # following PATH later and picking up an older global version.
    arguments = f'/k ""{sys.executable}" -m termapy "{cfg_path}""'
    ico_path = _TEMPLATES_DIR / "icons" / "termapy.ico"
    cmd_exe = r"C:\Windows\System32\cmd.exe"

    overwrite_guard = "" if force else (
        "if (Test-Path $path) { Write-Error ('EXISTS:' + $path); exit 1 };"
    )
    ps = (
        "$ErrorActionPreference = 'Stop';"
        f"$desktop = {_PS_RESOLVE_DESKTOP};"
        f"$path = Join-Path $desktop {_ps_quote(filename)};"
        f"{overwrite_guard}"
        "$ws = New-Object -ComObject WScript.Shell;"
        "$lnk = $ws.CreateShortcut($path);"
        f"$lnk.TargetPath = {_ps_quote(cmd_exe)};"
        f"$lnk.Arguments = {_ps_quote(arguments)};"
        f"$lnk.WorkingDirectory = {_ps_quote(str(cfg_path.parent))};"
        f"$lnk.IconLocation = {_ps_quote(ico_path)};"
        "$lnk.Save();"
        "Write-Output $path"
    )

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"PowerShell invocation failed: {e}"

    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "EXISTS:" in err:
            return None, _already_exists(filename, on_desktop=True)
        return None, err or "PowerShell shortcut creation failed."

    created = proc.stdout.strip()
    return Path(created) if created else None, None


# ── Dispatch (create / remove / list) ────────────────────────────────────────


def _create(
    ctx: PluginContext, name: str, stem: str, cfg_path: Path, force: bool,
) -> CmdResult:
    handler = _by_platform(_create_linux, _create_macos, _create_windows)
    if handler is None:
        return CmdResult.fail(msg=f"Unsupported platform: {_PLATFORM}")
    path, err = handler(name, stem, cfg_path, force)
    if err:
        return CmdResult.fail(msg=err)
    if path is None:
        return CmdResult.fail(msg="Launcher creation returned no path.")
    ctx.io.result(f"Launcher created: {path}")
    return CmdResult.ok(value=str(path))


def _remove(ctx: PluginContext, name: str, stem: str) -> CmdResult:
    path_fn: Callable[[str], Path] | None = _by_platform(
        lambda _stem=stem: _linux_menu_path(stem),
        lambda _name=name: _macos_bundle_path(name),
        lambda _name=name: _windows_lnk_path(name),
    )
    if path_fn is None:
        return CmdResult.fail(msg=f"Unsupported platform: {_PLATFORM}")
    path = path_fn()
    if not path.exists():
        return CmdResult.fail(msg=f"Launcher not found: {path}")
    (shutil.rmtree if path.is_dir() else Path.unlink)(path)
    ctx.io.result(f"Launcher removed: {path}")
    return CmdResult.ok(value=str(path))


# ── Scan + list ──────────────────────────────────────────────────────────────


def _scan_linux() -> list[Path]:
    apps = Path.home() / ".local" / "share" / "applications"
    return sorted(apps.glob("termapy-*.desktop")) if apps.is_dir() else []


def _scan_macos() -> list[Path]:
    apps = Path.home() / "Applications"
    if not apps.is_dir():
        return []
    found: list[Path] = []
    for app in sorted(apps.glob("*.app")):
        plist = app / "Contents" / "Info.plist"
        if not plist.is_file():
            continue
        try:
            # Bundle-ID prefix is the unambiguous marker; every termapy
            # launcher carries net.termapy.<slug> in CFBundleIdentifier.
            if "net.termapy." in plist.read_text(
                encoding="utf-8", errors="ignore",
            ):
                found.append(app)
        except OSError:
            continue
    return found


def _enum_windows_launchers() -> list[tuple[Path, str]]:
    """Enumerate Desktop .lnks via one PowerShell call.

    Returns ``(lnk_path, arguments_string)`` pairs for every .lnk
    whose Arguments mentions ``termapy``.  Callers that only need
    the paths use ``_scan_windows`` (a thin wrapper); callers that
    need to substring-match against the args (e.g. finding a
    launcher for a specific cfg) use this directly.
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
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    pairs: list[tuple[Path, str]] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        path_str, args = line.split("\t", 1)
        if "termapy" in args.lower():
            pairs.append((Path(path_str), args))
    return pairs


def _scan_windows() -> list[Path]:
    """Desktop .lnks that mention termapy (path-only)."""
    return [p for p, _ in _enum_windows_launchers()]


# ── Find a launcher for a specific cfg (cleanup on cfg-delete) ──────────────


def _find_linux_launcher_for_cfg(cfg_path: Path) -> Path | None:
    needle = str(cfg_path)
    for desktop_file in _scan_linux():
        try:
            if needle in desktop_file.read_text(
                encoding="utf-8", errors="ignore",
            ):
                return desktop_file
        except OSError:
            continue
    return None


def _find_macos_launcher_for_cfg(cfg_path: Path) -> Path | None:
    needle = str(cfg_path)
    for app in _scan_macos():
        launcher = app / "Contents" / "MacOS" / "launcher"
        try:
            if launcher.is_file() and needle in launcher.read_text(
                encoding="utf-8", errors="ignore",
            ):
                return app
        except OSError:
            continue
    return None


def _find_windows_launcher_for_cfg(cfg_path: Path) -> Path | None:
    needle = str(cfg_path)
    for path, args in _enum_windows_launchers():
        if needle in args:
            return path
    return None


def find_launcher_for_cfg(cfg_path: Path) -> Path | None:
    """Find any termapy launcher whose embedded args reference cfg_path.

    Returns the launcher's filesystem path (a ``.desktop`` file on
    Linux, an ``.app`` bundle directory on macOS, a ``.lnk`` on
    Windows) or ``None`` if no launcher references this cfg.

    Cfg path is matched as a verbatim substring against the
    launcher's embedded target/args, so callers should pass the
    same path form that ``/cfg.icon`` originally embedded
    (typically ``Path(ctx.config_path).resolve()``).
    """
    finder = _by_platform(
        _find_linux_launcher_for_cfg,
        _find_macos_launcher_for_cfg,
        _find_windows_launcher_for_cfg,
    )
    return finder(cfg_path) if finder is not None else None


def remove_launcher_at(launcher_path: Path) -> None:
    """Delete a launcher file or .app directory.  Silent if missing.

    Raises OSError if the deletion fails for permission/I-O reasons;
    callers decide whether to surface or swallow.
    """
    if launcher_path.is_dir():
        shutil.rmtree(launcher_path)
    elif launcher_path.exists():
        launcher_path.unlink()


def _list(ctx: PluginContext) -> CmdResult:
    scan, where = _by_platform(
        (_scan_linux,  "~/.local/share/applications/"),
        (_scan_macos,  "~/Applications/"),
        (_scan_windows, "Desktop"),
    ) or (None, None)
    if scan is None:
        return CmdResult.fail(msg=f"Unsupported platform: {_PLATFORM}")
    found = scan()
    if not found:
        ctx.io.result(f"No termapy launchers found in {where}.")
        return CmdResult.ok(value="")
    for path in found:
        ctx.io.output(f"  {path}")
    return CmdResult.ok(value="\n".join(str(p) for p in found))


# ── Handler entry points ─────────────────────────────────────────────────────


def _resolve_name(ctx: PluginContext) -> tuple[str, str, Path] | None:
    """Return (display_name, filename_stem, cfg_path), or None if no cfg."""
    if not ctx.config_path:
        return None
    cfg_path = Path(ctx.config_path).resolve()
    stem = cfg_path.stem
    name = str(ctx.cfg.get("title") or stem).strip() or stem
    return name, stem, cfg_path


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon`` -- create a desktop launcher for the current cfg."""
    resolved = _resolve_name(ctx)
    if resolved is None:
        return CmdResult.fail(msg="No config loaded.")
    return _create(ctx, *resolved, ctx.flag("--force"))


def _handler_remove(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon.remove`` -- delete the launcher for the current cfg."""
    resolved = _resolve_name(ctx)
    if resolved is None:
        return CmdResult.fail(msg="No config loaded.")
    name, stem, _ = resolved
    return _remove(ctx, name, stem)


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """``/cfg.icon.list`` -- list every termapy launcher this OS can see."""
    return _list(ctx)


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
           (a small bundle that opens Terminal.app and runs termapy)
  Windows  Desktop\\<title>.lnk
           (generated via PowerShell COM; target is
           ``cmd /k "<python>" -m termapy "<cfg file>"``)

The launcher embeds the absolute path to this .cfg file and to the
python that's currently running termapy, so it stays tied to this
install instead of following PATH later.  Rerun /cfg.icon to refresh
if you move/rename the cfg or change termapy installs.

All three platforms get the same custom termapy icon (DB9 connector
with a tapered cable receding to a near-vanishing point) rendered
from a single SVG source.  See src/termapy/templates/icons/termapy.svg.
"""


_FLAGS = {"--force": "Overwrite an existing launcher."}


__all__ = [
    "_FLAGS",
    "_LONG_HELP",
    "_handler",
    "_handler_list",
    "_handler_remove",
    "find_launcher_for_cfg",
    "remove_launcher_at",
]
