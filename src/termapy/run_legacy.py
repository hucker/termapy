"""Implementation of /run.legacy -- scan a .run script for legacy commands.

Walks a script file line by line, looks for command names that have
been renamed (``/echo`` -> ``/term.echo``, ``/show_line_endings`` ->
``/term.line_endings``, ...), and either reports the hits or rewrites
the file with ``--fix``.

Lives at ``termapy.run_legacy`` (outside ``builtins/commands/``) rather
than as an auto-loaded plugin because ``/run`` is registered as an
app-level hook in both CLI and TUI, and ``register_hook("run", ...)``
wipes every ``run.*`` plugin entry.  Keeping the handler as a plain
module lets both frontends register it as a sibling hook after
``/run`` is installed.

The rename tables -- ``LEGACY_COMMANDS`` (simple name renames) and
``LEGACY_REWRITES`` (args-aware) -- live in ``termapy.legacy`` and are
populated at import.  To add a rename, add it there (a
``LEGACY_FORWARDERS`` entry also gives it a runtime forwarder); this
scanner reads the tables, so it picks the rename up automatically.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.help_dynamic import folder_line
from termapy.legacy import LEGACY_COMMANDS, LEGACY_REWRITES
from termapy.plugins import CmdResult, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _scan_line(line: str, prefix: str) -> tuple[str, list[tuple[str, str]]]:
    """Scan one line for legacy commands.

    Returns ``(rewritten_line, hits)`` where ``hits`` is a list of
    ``(old_text, new_text)`` pairs found in this line.  Only matches a
    command name at the start of the (possibly prefix-stripped) line
    or immediately after the REPL prefix.  ``LEGACY_REWRITES`` (regex,
    args-aware) is checked first so a longer rewrite wins over a
    name-only match (e.g. ``verbose on`` -> ``term.output verbose``
    beats the bare ``verbose`` -> ``term.output`` rename).
    """
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped.startswith(prefix):
        return line, []
    body = stripped[len(prefix):]
    # Args-aware rewrites first.  Each pattern matches the body (no
    # prefix); the first hit wins.  We rebuild the indent + prefix
    # around the substituted body so leading whitespace is preserved.
    for pat, repl in LEGACY_REWRITES:
        new_body, n = pat.subn(repl, body, count=1)
        if n:
            old_match = pat.search(body)
            old_text = old_match.group(0) if old_match else body
            return (
                f"{indent}{prefix}{new_body}",
                [(old_text, pat.sub(repl, old_text))],
            )
    # Simple name renames from LEGACY_COMMANDS.  Split the name off at
    # a word boundary so we don't eat args.
    m = re.match(r"([A-Za-z_][\w.]*)", body)
    if not m:
        return line, []
    name = m.group(1)
    new_name = LEGACY_COMMANDS.get(name)
    if new_name is None:
        return line, []
    rest = body[len(name):]
    return f"{indent}{prefix}{new_name}{rest}", [(name, new_name)]


def _resolve_script(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a script filename the same way /run does.

    Checks ``<scripts_dir>/<name>`` first; falls back to cwd.  Appends
    ``.run`` if the user omitted it.
    """
    raw = name.strip()
    if not raw:
        return None
    if not raw.endswith(".run") and not Path(raw).suffix:
        raw += ".run"
    candidate = Path(ctx.fs.scripts_dir) / raw
    if candidate.is_file():
        return candidate
    cwd_candidate = Path.cwd() / raw
    if cwd_candidate.is_file():
        return cwd_candidate
    return None


def _scan_one_file(
    ctx: PluginContext, path: Path, prefix: str, fix_mode: bool
) -> int:
    """Scan ``path`` and report (or rewrite).  Returns the hit count.

    Writes per-file status to the terminal via ``ctx``.  A return of 0
    means clean; a positive number is how many legacy commands were
    matched in this file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        ctx.io._write(f"  {path.name}: read error: {e}", "red")
        return 0

    lines = text.splitlines(keepends=True)
    rewritten_lines: list[str] = []
    hits: list[tuple[int, str, str, str]] = []  # (lineno, old, new, original)
    for i, line in enumerate(lines, 1):
        # Strip trailing newline for display but keep it in output.
        eol = ""
        if line.endswith("\r\n"):
            eol = "\r\n"
            body = line[:-2]
        elif line.endswith("\n"):
            eol = "\n"
            body = line[:-1]
        else:
            body = line
        new_body, line_hits = _scan_line(body, prefix)
        rewritten_lines.append(new_body + eol)
        for old, new in line_hits:
            hits.append((i, old, new, body))

    if not hits:
        ctx.io._write(f"  {path.name}: no legacy commands found.", "green")
        return 0

    if fix_mode:
        try:
            path.write_text("".join(rewritten_lines), encoding="utf-8")
        except OSError as e:
            ctx.io._write(f"  {path.name}: write error: {e}", "red")
            return 0
        ctx.io._write(f"  {path.name}: rewrote {len(hits)} line(s).", "green")
        for lineno, old, new, _original in hits:
            ctx.io._write_markup(
                f"    line {lineno}: [yellow]{prefix}{old}[/] -> "
                f"[green]{prefix}{new}[/]"
            )
        return len(hits)

    ctx.io._write(
        f"  {path.name}: {len(hits)} legacy command(s) found "
        f"(run with --fix to rewrite):",
        "yellow",
    )
    for lineno, old, new, original in hits:
        ctx.io._write_markup(
            f"    line {lineno}: [yellow]{prefix}{old}[/] -> "
            f"[green]{prefix}{new}[/]"
        )
        ctx.io._write(f"      {original}", "dim")
    return len(hits)


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Report legacy commands in a script file (or all with ``*``).

    With ``--fix``, rewrites the file(s) in place.
    """
    fix_mode = ctx.flag("--fix")
    name = args.strip()
    if not name:
        raise UsageError()

    prefix = ctx.prefix

    # ``*`` scans every .run file in the config's scripts directory.
    if name == "*":
        scripts_dir = Path(ctx.fs.scripts_dir)
        if not scripts_dir.is_dir():
            return CmdResult.fail(msg=f"Scripts directory not found: {scripts_dir}")
        paths = sorted(scripts_dir.glob("*.run"))
        if not paths:
            ctx.io._write(f"  No .run files in {scripts_dir}.", "yellow")
            return CmdResult.ok(value="0")
        total_hits = 0
        total_files_with_hits = 0
        for path in paths:
            hits = _scan_one_file(ctx, path, prefix, fix_mode)
            total_hits += hits
            if hits:
                total_files_with_hits += 1
        ctx.io._write("")
        verb = "rewrote" if fix_mode else "found"
        ctx.io._write(
            f"  Summary: {verb} {total_hits} legacy command(s) "
            f"across {total_files_with_hits}/{len(paths)} file(s).",
            "green" if fix_mode else "yellow",
        )
        return CmdResult.ok(value=str(total_hits))

    # Single-file mode.
    path = _resolve_script(ctx, name)
    if path is None:
        return CmdResult.fail(msg=f"Script not found: {name}")
    hits = _scan_one_file(ctx, path, prefix, fix_mode)
    return CmdResult.ok(value=str(hits))


def _run_long_help(ctx: PluginContext) -> str:
    """Show current legacy table + script folder count."""
    mapping_lines = [
        f"  {ctx.prefix}{old} -> {ctx.prefix}{new}"
        for old, new in sorted(LEGACY_COMMANDS.items())
    ]
    folder = folder_line(ctx, "run", noun="script")
    body = (
        "Scan a .run script for command names that have been renamed.\n"
        "Pass a filename to scan one file, or ``*`` to scan every\n"
        ".run file in the config's scripts/ directory.\n"
        "Without --fix, reports the hits line by line.  With --fix,\n"
        "rewrites the file(s) in place.\n"
        "\n"
        "Current rename table:\n"
        + "\n".join(mapping_lines)
    )
    return folder + "\n\n" + body if folder else body


# ── Public exports used by register_hook in cli.py and app.py ─────────────────
#
# cli.py / app.py each register /run AFTER loading plugins; that
# wipes every run.* plugin entry.  They re-install run.legacy
# afterwards by pulling these three names.

HANDLER = _handler
HELP = "Scan a .run script for legacy command names (report, or --fix to rewrite)."
LONG_HELP = _run_long_help
ARGS = "<filename|*> {--fix}"
FLAGS = {"--fix": "Rewrite the file in place instead of just reporting."}
