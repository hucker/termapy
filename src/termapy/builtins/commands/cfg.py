"""Built-in plugin: show or change config values, project info, file listings."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._cfg_icon import (
    _FLAGS as _ICON_FLAGS,
    _LONG_HELP as _ICON_LONG_HELP,
    _handler as _handler_icon,
    _handler_list as _handler_icon_list,
    _handler_remove as _handler_icon_remove,
    retire_launcher,
)
from termapy.builtins.commands.edit import edit_cfg_command
from termapy.config import (
    ConfigExistsError,
    cfg_data_dir,
    cfg_dir,
    cfg_history_path,
    cfg_path_for_name,
    config_summary,
    create_config,
    delete_config,
    delete_config_question,
    global_plugins_dir,
    new_config_from_args,
    open_with_system,
    rename_config,
)
from termapy.config_resolve import resolve_config
from termapy.folder_ops import FILE_COLUMNS, file_record, list_entries
from termapy.folders import FOLDERS, INFO_REPORT_SUFFIX
from termapy.help_dynamic import cfg_status, compose
from termapy.plugins import CapabilitySet, CmdResult, Column, Command, UsageError, escape_markup
from termapy.plugins.params import ParamSpec
from termapy.scripting import coerce_to_type

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── /cfg handler ───────────────────────────────────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show all config, a single key, or set a key with confirmation.

    With no arguments, prints every key/value pair. With a key only,
    prints that key's current value. With key and value, validates the
    type against the existing value, then asks the frontend to confirm
    and answers after it does (Cancel fails), or applies directly where
    the frontend has no dialog.

    Args:
        ctx: Plugin context for config access and output.
        args: Optional ``"key"`` or ``"key value"`` string.
    """
    # Hand-rolled (not declarative params, unlike the /cfg.auto sibling): this
    # is a variable-arity mode dispatch -- bare /cfg opens the picker, `/cfg key`
    # queries, `/cfg key value` sets -- which params' fixed synopsis can't
    # express (see CLAUDE.md "Declarative Command Parameters").  /cfg.load is
    # likewise a trivial single positional kept hand-rolled.
    parts = args.strip().split(None, 1)
    # Bare /cfg never arrives: the dispatcher opens the Cfg picker in the
    # TUI (picker="cfg") and runs /cfg.list elsewhere (bare_sub="list").
    # Use /cfg.dump to print the loaded config as JSON.
    if not parts:
        raise UsageError()
    key = parts[0]
    container = _find_cfg_container(ctx.cfg, key)
    if container is None:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    # /cfg key - show value
    if len(parts) == 1:
        val = container[key]
        ctx.io.result(str(val))
        return CmdResult.ok(value=str(val))
    # /cfg key value - validate and delegate for confirmation
    value_str = parts[1]
    try:
        new_val = coerce_to_type(value_str, container[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    old_val = container[key]
    if new_val == old_val:
        ctx.io.output(f"{key} is already {old_val!r}")
        return CmdResult.ok(value=str(old_val))
    # The TUI asks first and answers only after the dialog does, so a
    # script line after /cfg sees the outcome (review 2026-10-15, D7).
    if ctx.internal.confirm_save_cfg:
        if not ctx.internal.confirm_save_cfg(key, new_val):
            return CmdResult.fail(msg="Canceled.")
    else:
        ctx.internal.apply_cfg(key, new_val)
    return CmdResult.ok(value=str(new_val))


def _find_cfg_container(cfg, key: str):
    """Return the dict that owns ``key`` (top-level or under cfg["serial"]).

    Lets ``/cfg port`` keep working post-v22 even though port now lives
    under ``cfg["serial"]``.  ``None`` means the key isn't known.  Only
    looks one level deep (top + serial sub-dict); deeper nesting would
    require dotted-path support.

    ``cfg`` is typed loosely (``MappingProxyType | dict`` in callers)
    so the helper just relies on duck-typed ``in`` / ``get``.
    """
    if key in cfg:
        return cfg
    serial = cfg.get("serial", {})
    if isinstance(serial, dict) and key in serial:
        return serial
    return None


# ── /cfg.auto handler ─────────────────────────────────────────────────────────

# Cfg keys whose value is a filesystem path.  Setting one to an
# out-of-sandbox path turns a later read/load into an arbitrary-file
# primitive (e.g. log_file -> /log.dump), so they are contained under
# the MCP sandbox like any other caller-supplied path.
_PATH_VALUED_CFG_KEYS = frozenset({"log_file", "profile_path"})


def _handler_auto(ctx: PluginContext, args: str) -> CmdResult:
    """Set a config key immediately without confirmation dialog.

    Parameters arrive via ``ctx.arg`` (see ``params``); ``value`` is a
    positional-rest so a config value may contain spaces.
    """
    key = ctx.arg("key")
    value_str = ctx.arg("value")
    container = _find_cfg_container(ctx.cfg, key)
    if container is None:
        return CmdResult.fail(msg=f"Unknown config key: {key}")
    if key in _PATH_VALUED_CFG_KEYS and value_str:
        ctx.fs.guard_external_path(value_str, f"cfg {key}")
    try:
        new_val = coerce_to_type(value_str, container[key])
    except (ValueError, TypeError) as e:
        return CmdResult.fail(msg=f"Type error: {e}")
    ctx.internal.apply_cfg(key, new_val)
    return CmdResult.ok(value=str(new_val))


# ── /cfg.list handler ─────────────────────────────────────────────────────────


def _handler_list(ctx: PluginContext, args: str) -> CmdResult:
    """List the configs newest first: the Cfg picker's rows, ``*`` on the loaded one.

    ``value`` is the config names (what ``/cfg.load`` takes); each data
    record is the file's ``file_record`` plus ``file``, ``active`` and
    ``config.config_summary``'s port, baud and title.

    Args:
        ctx: Plugin context for output.
        args: Unused.
    """
    root = cfg_dir()
    paths = list_entries(root, "*/*.cfg")
    records = [
        {
            **file_record(path),
            "name": path.stem,
            "file": f"{path.parent.name}/{path.name}",
            "active": str(path) == ctx.config_path,
            **config_summary(path),
        }
        for path in paths
    ]
    columns = (
        Column("NAME", lambda record: record["name"] + (" *" if record["active"] else "")),
        *FILE_COLUMNS[1:],
        Column("PORT", lambda record: record["port"]),
        Column("BAUD", lambda record: str(record["baud_rate"] or ""), right=True),
        Column("TITLE", lambda record: record["title"]),
    )
    names = "\n".join(path.stem for path in paths)
    return CmdResult.table(f"{root.name}/", "config", columns, records, value=names)


# ── /cfg.load handler ──────────────────────────────────────────────────────


def _handler_load(ctx: PluginContext, args: str) -> CmdResult:
    """Load a different config in the current session.

    Accepts a bare config name (``myproj``), a relative path, or an
    absolute path -- same resolution as the ``[config]`` positional
    command-line argument.  Disconnects any currently-open port,
    replaces the in-memory config, and reconnects if the new cfg has
    ``auto_connect: true``.

    Available in CLI, TUI, and MCP frontends.  Particularly useful in
    MCP mode where the model can hot-swap between device configs
    without spawning a new server process for each one.

    Args:
        ctx: Plugin context (uses ``ctx.internal.load_config``).
        args: The config name / path to load.
    """
    name = args.strip()
    if not name:
        raise UsageError()
    # A bare name / in-sandbox relative path (the MCP hot-swap case) is
    # allowed; an absolute path or ``..`` -- which load_config would read
    # AND rewrite with defaults, clobbering any JSON file -- is refused
    # under the MCP sandbox.
    ctx.fs.guard_external_path(name, "Config")
    return ctx.internal.load_config(name)


def _handler_new(ctx: PluginContext, args: str) -> CmdResult:
    """Create config NAME for PORT where ``/cfg.list`` looks; ``/cfg.load`` switches to it.

    Read and written exactly as ``termapy --new`` is
    (``config.new_config_from_args``, ``config.create_config``), so the
    command, the flag and the New Config wizard make the same files.  It
    creates and does not load: loading is ``/cfg.load``'s job, and a
    script that keeps running after it keeps its state.  A config already
    there is replaced only on a yes to the shared question, and then only
    its ``.cfg``; a host with no one to ask (MCP) refuses instead.  Bare,
    the TUI opens the New Config wizard (``picker="setup"``).

    Args:
        ctx: Plugin context.
        args: ``NAME PORT [SETTING ...]``.
    """
    # Hand-rolled (CLAUDE.md "Declarative Command Parameters"): the
    # settings are /port.connect's grammar, read by its own reader.
    name, _, settings = args.strip().partition(" ")
    if not name:
        raise UsageError()
    try:
        cfg = new_config_from_args(name, settings)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))
    path = cfg_path_for_name(name)
    overwritten = False
    try:
        try:
            create_config(path, cfg)
        except ConfigExistsError as e:
            if not ctx.capabilities.confirm_dialog:
                return CmdResult.fail(msg=str(e))
            if not ctx.ui.confirm(e.question):
                return CmdResult.fail(msg=f"Config not changed: {name}")
            create_config(path, cfg, overwrite=True)
            overwritten = True
    except (ValueError, RuntimeError, OSError) as e:
        return CmdResult.fail(msg=str(e))
    if overwritten:
        ctx.io.result(f"Replaced {path.name}; the other files in {name}/ are kept.", "green")
    else:
        ctx.io.result(f"Created config {name}.", "green")
    return CmdResult.ok(
        value=str(path),
        data={"name": name, "path": str(path), "overwritten": overwritten},
    )


def _other_config(ctx: PluginContext, arg: str, verb: str) -> Path | CmdResult:
    """The ``.cfg`` that ``arg`` names, or the failure: not found, or the active one.

    Resolved as ``/cfg.load`` resolves (a name, a relative path, or -- outside
    the MCP sandbox -- an absolute one).  The active config is refused: its
    folder holds the session's open log and history, so it cannot be moved
    or removed under the running session.
    """
    ctx.fs.guard_external_path(arg, "Config")
    found = resolve_config(arg)
    if found is None or Path(found).suffix != ".cfg":
        return CmdResult.fail(msg=f"Config not found: {arg}")
    path = Path(found)
    if ctx.config_path and Path(ctx.config_path).resolve() == path.resolve():
        return CmdResult.fail(msg=f"Cannot {verb} the active config: {path.stem}")
    return path


def _handler_delete(ctx: PluginContext, args: str) -> CmdResult:
    """Delete a config and its folder, after a yes (``config.delete_config``).

    The one delete in every frontend: the config picker's Delete button
    dispatches this command.  The TUI and CLI ask the shared question; MCP
    asks through its destructive gate (``confirm=true``) instead, since it
    has no prompt of its own.
    """
    arg = args.strip()
    if not arg:
        raise UsageError()
    path = _other_config(ctx, arg, "delete")
    if isinstance(path, CmdResult):
        return path
    if ctx.capabilities.confirm_dialog and not ctx.ui.confirm(delete_config_question(path)):
        return CmdResult.fail(msg=f"Config not deleted: {path.stem}")
    try:
        count = delete_config(path)
    except (OSError, RuntimeError) as e:
        return CmdResult.fail(msg=f"Delete failed: {e}")
    noun = "file" if count == 1 else "files"
    ctx.io.result(f"Deleted config {path.stem} ({count} {noun}).", "green")
    # A launcher pointing at a deleted config is a dead link.
    retired = retire_launcher(path)
    if retired is not None:
        ctx.io.result(*retired)
    return CmdResult.ok(value=str(path), data={"name": path.stem, "files": count})


def _handler_rename(ctx: PluginContext, args: str) -> CmdResult:
    """Rename a config, folder and file together (``config.rename_config``)."""
    parts = args.strip().rsplit(maxsplit=1)
    if len(parts) != 2:
        raise UsageError()
    arg, new_name = parts
    path = _other_config(ctx, arg, "rename")
    if isinstance(path, CmdResult):
        return path
    try:
        new_path = rename_config(str(path), new_name)
    except (ValueError, OSError) as e:
        return CmdResult.fail(msg=f"Rename failed: {e}")
    ctx.io.result(f"Renamed config: {path.stem} -> {Path(new_path).stem}", "green")
    # The launcher embeds the old path, so it is a dead link now.
    retired = retire_launcher(path)
    if retired is not None:
        ctx.io.result(*retired)
    return CmdResult.ok(value=new_path)


# ── /cfg.explore handler ──────────────────────────────────────────────────────


def _handler_dump(ctx: PluginContext, args: str) -> CmdResult:
    """Print the config in memory as JSON; it needs no config file.

    Lets `.quiet` mode capture the output for scripting
    (e.g. ``$(JSON) <- /cfg.dump.quiet``).  A file from the config
    folder is ``/cfg.file <name>``: until 2026-10-16 it was this command
    with a name, which needed a config where the bare form did not, so
    the handler had to check for one itself.
    """
    if args.strip():
        raise UsageError(f"For a file in the config folder use {ctx.prefix}cfg.file <name>")
    text = json.dumps(dict(ctx.cfg), indent=4)
    ctx.io.output(text)
    return CmdResult.ok(value=text)


def _handler_file(ctx: PluginContext, args: str) -> CmdResult:
    """Print a file from the active config folder to the terminal.

    Names resolve RELATIVE TO THE CFG ROOT ONLY (subfolders allowed):
    absolute paths and ``..`` traversal are refused for every frontend,
    so this can never read outside the config folder.
    """
    name = ctx.arg("name")
    root = Path(ctx.config_path).parent.resolve()
    rel = Path(name)
    path = (root / rel).resolve()
    # One refusal for every way out: an absolute name, any "..", and a
    # symlink that resolves outside the folder.
    if rel.is_absolute() or ".." in rel.parts or not path.is_relative_to(root):
        return CmdResult.fail(msg=f"Path must be relative to the config folder: {name}")
    if not path.is_file():
        return CmdResult.fail(msg=f"File not found: {name}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return CmdResult.fail(msg=f"Read error: {e}")
    for line in text.splitlines():
        ctx.io.output(line)
    return CmdResult.ok(value=text)


def _handler_explore(ctx: PluginContext, args: str) -> CmdResult:
    """Open the config data directory in the system file explorer.

    Args:
        ctx: Plugin context.
        args: Unused.
    """
    data_dir = Path(ctx.config_path).parent
    open_with_system(str(data_dir))
    return CmdResult.ok(value=data_dir)


def _handler_show(ctx: PluginContext, args: str) -> CmdResult:
    """Open the current config file in the system viewer.

    Mirrors ``/edit.cfg``; named ``cfg.show`` for symmetry with the
    folder ``.show`` family on /run, /proto, /ss, etc.
    """
    ctx.fs.open_file(Path(ctx.config_path))
    return CmdResult.ok(value=Path(ctx.config_path))


def _handler_help(ctx: PluginContext, args: str) -> CmdResult:
    """Same as ``/help cfg``, plus an AVAILABLE CONFIGS file list."""
    from termapy.builtins.commands.help import (
        _show_command_help,
        append_files_section,
    )

    result = _show_command_help(ctx, "cfg")
    files = sorted(f"{path.parent.name}/{path.name}" for path in cfg_dir().glob("*/*.cfg"))
    append_files_section(ctx, "AVAILABLE CONFIGS", files)
    return result


# ── Tree-building helpers (shared by info and folder listings) ────────────────


def _names(directory: Path, pattern: str) -> list[str]:
    """Return sorted filenames matching pattern in directory."""
    if pattern == "*":
        return sorted(path.name for path in directory.glob(pattern) if path.is_file())
    return sorted(path.name for path in directory.glob(pattern))


def _build_tree(
    config_path: str,
    sections: list[tuple[str, list[str]]],
    global_names: list[str] | None = None,
) -> tuple[str, str]:
    """Build plain and Rich-colored directory trees.

    The colored tree is for the terminal output and contains names
    only.  The plain tree is for the markdown report and appends
    size / created / modified columns to each file line; folders
    appear bare.  Columns are space-padded so they align in a
    monospace ``text`` code-fence.

    Args:
        config_path: Path to the config file.
        sections: List of (name, file_list) tuples.
        global_names: Optional global plugin filenames.

    Returns:
        Tuple of (colored tree for terminal, plain tree for markdown).
    """
    from termapy.tree_render import FileTree

    abs_root = Path(config_path).parent.resolve().as_posix() + "/"
    data_dir = Path(config_path).parent

    # Filter to entries that exist on disk; bare files that don't
    # exist are skipped so the tree never claims a missing artifact.
    entries: list[tuple[str, list[str]]] = []
    for name, files in sections:
        if name.endswith("/"):
            entries.append((name, files))
        elif (data_dir / name).exists():
            entries.append((name, []))

    # Pad all file names to the longest one (across both top-level and
    # nested files, plus any global plugins) so the metadata columns
    # line up in the monospace markdown fence.
    name_width = 0
    for name, files in entries:
        if not name.endswith("/"):
            name_width = max(name_width, len(name))
        else:
            for fname in files:
                name_width = max(name_width, len(fname))
    for fname in global_names or []:
        name_width = max(name_width, len(fname))

    color_lines = [f"[cyan]{escape_markup(abs_root)}[/]"] + FileTree(
        entries,
        base_dir=data_dir,
        color=True,
    ).render()
    plain_lines = [abs_root] + FileTree(
        entries,
        base_dir=data_dir,
        file_dates=True,
        color=False,
        name_width=name_width,
    ).render()

    if global_names:
        global_sections = [(fname, []) for fname in global_names]
        color_lines.append("")
        color_lines.append("[cyan]plugin/ (global)[/]")
        color_lines.extend(
            FileTree(
                global_sections,
                base_dir=global_plugins_dir(),
                color=True,
            ).render()
        )

        plain_lines.append("")
        plain_lines.append("plugin/ (global)")
        plain_lines.extend(
            FileTree(
                global_sections,
                base_dir=global_plugins_dir(),
                file_dates=True,
                color=False,
                name_width=name_width,
            ).render()
        )

    return "\n".join(color_lines), "\n".join(plain_lines)


def _all_sections(config_path: str) -> list[tuple[str, list[str]]]:
    """Build the sections list for a config: cfg, log, and every data folder that exists.

    Data folders appear on first write, so an absent one is not listed --
    the tree shows what is on disk, not the maximum layout.
    """
    data_dir = Path(config_path).parent
    config_name = Path(config_path).stem
    sections: list[tuple[str, list[str]]] = [
        (f"{config_name}.cfg", []),
        (f"{config_name}.log", []),
    ]
    for spec in FOLDERS:
        folder = data_dir / spec.name
        if folder.is_dir():
            sections.append((f"{spec.name}/", _names(folder, spec.pattern)))
    return sections


# ── /cfg.info handler ──────────────────────────────────────────────────────────


# Trailing lines of the session log and the command history the report keeps.
_TAIL_LINES = 40


def _tail_section(title: str, path: Path) -> list[str]:
    """``## <title> (last N lines)`` over the end of ``path``, or nothing.

    A missing or empty file yields no section rather than an empty one.
    The fence is four backticks: device output can contain a
    three-backtick line, and a longer fence legitimately contains a
    shorter one.
    """
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return []
    kept = lines[-_TAIL_LINES:]
    return [
        f"## {title} (last {len(kept)} lines)",
        "",
        "````text",
        *kept,
        "````",
        "",
    ]


def write_info_report(ctx: PluginContext, *, trigger: str) -> tuple[Path, str]:
    """Write ``<config>.md`` beside the config; return its path and the coloured tree.

    A ``Written`` line with the time and ``trigger`` (what caused the
    write), then folder tree, config, active custom buttons, and the tails
    of the session log and the command history.  Shared by ``/cfg.info``
    and the exit hook below, so the two can never disagree about what the
    report holds -- only about who wrote it, which the line records.

    Args:
        ctx: Plugin context.
        trigger: Phrase completing "Written <time> ...", e.g. ``"at exit"``.

    Raises:
        OSError: The folder could not be scanned or the file written.
        TypeError: The config holds a value ``json.dumps`` cannot encode.
        RuntimeError: The config is a bundled read-only template.
    """
    sections = _all_sections(ctx.config_path)
    global_names = _names(global_plugins_dir(), "*.py")
    colored_tree, plain_tree = _build_tree(ctx.config_path, sections, global_names)

    cfg_display = {k: v for k, v in ctx.cfg.items() if k != "custom_buttons"}
    buttons = ctx.cfg.get("custom_buttons", [])
    active = [button for button in buttons if button.get("enabled")]
    config_name = Path(ctx.config_path).stem

    md_lines: list[str] = [
        f"# Project: {config_name}",
        "",
        f"Written {datetime.now().isoformat(timespec='seconds')} {trigger}.",
        "",
        "```text",
        plain_tree,
        "```",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(cfg_display, indent=4),
        "```",
        "",
    ]
    if active:
        md_lines.extend(
            [
                f"## Custom Buttons ({len(active)} active)",
                "",
                "```json",
                json.dumps(active, indent=4),
                "```",
                "",
            ]
        )
    session_log = ctx.internal.session_log_path()
    if session_log is not None:
        md_lines.extend(_tail_section("Log", session_log))
    md_lines.extend(_tail_section("History", Path(cfg_history_path(ctx.config_path))))

    data_dir = cfg_data_dir(ctx.config_path)
    report_path = data_dir / f"{config_name}{INFO_REPORT_SUFFIX}"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    return report_path, colored_tree


def _handler_info(ctx: PluginContext, args: str) -> CmdResult:
    """Write the project report and print the folder tree.

    With ``--display``, also opens the report in the system viewer.

    Args:
        ctx: Plugin context.
        args: ``"--display"`` to open report externally.
    """
    try:
        report_path, colored_tree = write_info_report(ctx, trigger="by /cfg.info")
        ctx.io.output_markup(colored_tree)
        if ctx.flag("--display"):
            open_with_system(str(report_path))
    except OSError as e:
        # File write / directory scan failure -- most likely real
        # failure mode.  Reports the underlying OS message ("Permission
        # denied", "No such file or directory") to the user.
        return CmdResult.fail(msg=f"Info error: {e}")
    except TypeError as e:
        # json.dumps() hit a non-serializable value in the config.
        # Worth a specific message so the user knows what to fix.
        return CmdResult.fail(msg=f"Config has non-JSON value: {e}")
    return CmdResult.ok(value=report_path)


def on_app_stop(ctx: PluginContext) -> None:
    """Rewrite the report at exit so it reflects the session just ended.

    Interactive sessions only, and never the one-shot ``--run`` / ``--exec``
    forms: a server loading a config, or a script, must not write into the
    config folder as a side effect.  Silent, including on failure -- the
    explicit command is the loud form.  The TUI saves history and flushes
    the log before firing this, so the tails are this session's.
    """
    if not ctx.config_path or ctx.is_oneshot() or not ctx.capabilities.interactive:
        return
    try:
        write_info_report(ctx, trigger="at exit")
    except (OSError, TypeError, RuntimeError):
        pass


# ── Dynamic long_help ─────────────────────────────────────────────────────────

_CFG_PROSE = """\
Three modes:
  {prefix}cfg                  - TUI: open Cfg picker.  CLI: list configs.
  {prefix}cfg baud_rate        - show current value of 'baud_rate'
  {prefix}cfg baud_rate 115200 - change with confirmation dialog

Type is auto-detected from the existing value (int, float,
bool, string). Bool accepts: true/false, yes/no, on/off, 1/0.
Changes are saved to the JSON config file.

Use {prefix}cfg.dump to print every key/value pair as JSON.
Use {prefix}cfg.auto to set values without confirmation (for scripts)."""


def _cfg_long_help(ctx: PluginContext) -> str:
    """Green status line (active cfg + total count) prepended to the prose."""
    return compose(cfg_status(ctx), _CFG_PROSE)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cfg",
    args="{key {value}}",
    help="Show or set config values.",
    long_help=_cfg_long_help,
    handler=_handler,
    picker="cfg",
    bare_sub="list",
    sub_commands={
        "auto": Command(
            help="Set immediately (no confirmation).",
            handler=_handler_auto,
            params=[
                ParamSpec("key", "str", positional=True, required=True, help="config key to set"),
                ParamSpec(
                    "value",
                    "str",
                    positional=True,
                    rest=True,
                    required=True,
                    help="new value (may contain spaces)",
                ),
            ],
        ),
        "list": Command(
            help="List all config files.",
            handler=_handler_list,
        ),
        "load": Command(
            args="<name>",
            help="Load a different config in this session (hot-swap).",
            handler=_handler_load,
        ),
        "new": Command(
            args="<name> <port> {baud} {mode}",
            help="Create a config for a port; {prefix}cfg.load <name> switches to it.",
            long_help=(
                "Create config <name> (title <name>) for <port>, where "
                "{prefix}cfg.list looks, set to connect when it loads.  The "
                "settings after the port are {prefix}port.connect's: baud, "
                "frame (8N1 or N81), cr|lf|crlf, echo|noecho; unset ones are "
                "115200 8N1.  It creates and does not load: {prefix}cfg.load "
                "<name> switches to it.\n"
                "\n"
                "A config already there is replaced only after you say yes, "
                "and then only its .cfg; the scripts and data in its folder "
                "stay.  MCP has no one to ask, so it refuses.  termapy --new "
                "NAME PORT [SETTING ...] does the same from the command line "
                "and starts the new config."
            ),
            handler=_handler_new,
            picker="setup",
        ),
        "delete": Command(
            args="<name>",
            help="Delete a config and its whole folder (asks first).",
            long_help=(
                "Delete config <name> and everything in its folder: the .cfg, "
                "run scripts, profile, logs and captures.  Asks first in the "
                "TUI and CLI; MCP needs confirm=true.  The active config "
                "cannot be deleted -- load another first.  A config that "
                "another program holds open is refused with nothing removed.  "
                "Its desktop launcher, if any, goes too."
            ),
            handler=_handler_delete,
            safety="destructive",
        ),
        "rename": Command(
            args="<name> <new>",
            help="Rename a config, folder and file together.",
            long_help=(
                "Rename config <name> to <new>: its folder, its .cfg and every "
                "stem-named file (history, log, profile, symbols) move "
                "together.  Nothing is overwritten.  The active config cannot "
                "be renamed here (the TUI's Rename button can, and reloads "
                "it); its desktop launcher, if any, is removed -- "
                "{prefix}cfg.icon makes a new one."
            ),
            handler=_handler_rename,
        ),
        "info": Command(
            args="",
            flags={"--display": "Open full report in the system viewer."},
            help="Print project summary (tree + config + buttons).",
            handler=_handler_info,
            needs=CapabilitySet.CONFIG_LOADED,
        ),
        "explore": Command(
            help="Open config directory in file explorer.",
            handler=_handler_explore,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
        "edit": edit_cfg_command(),
        "dump": Command(
            help="Print the config in memory as JSON to the terminal.",
            handler=_handler_dump,
        ),
        "file": Command(
            help="Print a file from the config folder to the terminal.",
            handler=_handler_file,
            needs=CapabilitySet.CONFIG_LOADED,
            params=[
                ParamSpec(
                    "name",
                    "path",
                    positional=True,
                    required=True,
                    rest=True,
                    help="file relative to the config folder (subfolders allowed)",
                ),
            ],
        ),
        "show": Command(
            help="Open the current config file in the system viewer.",
            handler=_handler_show,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
        "help": Command(
            help="Show /cfg help.",
            handler=_handler_help,
        ),
        # Desktop/menu launchers only make sense on a graphical host, and
        # creating one spawns a subprocess (PowerShell on Windows).  Gate
        # the whole family on gui_apps -- like /cfg.show / /cfg.explore --
        # so a headless MCP peer can't write a launcher or spawn a process.
        # (needs is per-command: subcommands don't inherit the parent's.)
        "icon": Command(
            args="{--force}",
            help="Create a desktop / menu launcher for the current cfg.",
            long_help=_ICON_LONG_HELP,
            handler=_handler_icon,
            flags=_ICON_FLAGS,
            needs=CapabilitySet.CONFIG_GUI_APPS,
            sub_commands={
                "remove": Command(
                    help="Delete the launcher for the current cfg.",
                    handler=_handler_icon_remove,
                    needs=CapabilitySet.CONFIG_GUI_APPS,
                ),
                "list": Command(
                    help=("List every termapy launcher this platform can see."),
                    handler=_handler_icon_list,
                    needs=CapabilitySet.GUI_APPS,
                ),
            },
        ),
    },
)
