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

from termapy.folders import FOLDER_EXTS, FOLDER_PATTERNS, RUN, default_file_name
from termapy.help_dynamic import folder_line
from termapy.legacy import LEGACY_COMMANDS, LEGACY_REWRITES
from termapy.markup import escape_markup
from termapy.matching import is_glob, matches
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
    body = stripped[len(prefix) :]
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
    rest = body[len(name) :]
    return f"{indent}{prefix}{new_name}{rest}", [(name, new_name)]


def _resolve_script(ctx: PluginContext, name: str) -> Path | None:
    """Resolve a script filename.

    Checks ``run/<name>`` first, then the working folder; either may leave
    off ``.run``.  A host confined to the config's folder has only ``run/``,
    and ``ctx.fs.resolve`` refuses a name that leaves it.
    """
    raw = name.strip()
    if not raw:
        return None
    paths = [ctx.fs.resolve(raw, "scripts")]
    if ctx.capabilities.filesystem_unconfined:
        paths.append(Path.cwd() / raw)
    for path in paths:
        candidate = default_file_name(path, FOLDER_EXTS[RUN])
        if candidate.is_file():
            return candidate
    return None


def _scan_one_file(ctx: PluginContext, path: Path, prefix: str, fix_mode: bool) -> int:
    """Scan ``path`` and report (or rewrite).  Returns the hit count.

    Writes per-file status to the terminal via ``ctx``.  A return of 0
    means clean; a positive number is how many legacy commands were
    matched in this file.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        ctx.io.output(f"  {path.name}: read error: {e}", "red")
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
        ctx.io.output(f"  {path.name}: no legacy commands found.", "green")
        return 0

    if fix_mode:
        ctx.fs.guard_code_folder(path.parent)
        ctx.fs.guard_replace(path)
        try:
            path.write_text("".join(rewritten_lines), encoding="utf-8")
        except OSError as e:
            ctx.io.output(f"  {path.name}: write error: {e}", "red")
            return 0
        ctx.io.output(f"  {path.name}: rewrote {len(hits)} line(s).", "green")
        for lineno, old, new, _original in hits:
            ctx.io.output_markup(
                f"    line {lineno:d}: [yellow]{escape_markup(prefix + old)}[/] -> "
                f"[green]{escape_markup(prefix + new)}[/]"
            )
        return len(hits)

    ctx.io.output(
        f"  {path.name}: {len(hits)} legacy command(s) found (run with --fix to rewrite):",
        "yellow",
    )
    for lineno, old, new, original in hits:
        ctx.io.output_markup(
            f"    line {lineno:d}: [yellow]{escape_markup(prefix + old)}[/] -> "
            f"[green]{escape_markup(prefix + new)}[/]"
        )
        ctx.io.output(f"      {original}", "dim")
    return len(hits)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Report legacy commands in a script file, or in every one a pattern matches.

    The pattern is a name pattern over run/'s scripts (registry
    ``name-one-matcher``; ``*`` is every script), and one that matches
    nothing fails, as a missing name does.  With ``--fix``, rewrites the
    file(s) in place.
    """
    fix_mode = ctx.flag("--fix")
    name = args.strip()
    if not name:
        raise UsageError()

    prefix = ctx.prefix

    if is_glob(name):
        scripts_dir = Path(ctx.fs.scripts_dir)
        # A missing folder is an empty one: data folders appear on first write.
        paths = [
            path
            for path in sorted(scripts_dir.glob(FOLDER_PATTERNS[RUN]))
            if matches(path.name, name)
        ]
        if not paths:
            return CmdResult.fail(msg=f"No scripts in run/ match: {name}")
        total_hits = 0
        total_files_with_hits = 0
        for path in paths:
            hits = _scan_one_file(ctx, path, prefix, fix_mode)
            total_hits += hits
            if hits:
                total_files_with_hits += 1
        ctx.io.output("")
        verb = "rewrote" if fix_mode else "found"
        ctx.io.output(
            f"  Summary: {verb} {total_hits} legacy command(s) "
            f"across {total_files_with_hits}/{len(paths)} file(s).",
            "green" if fix_mode else "yellow",
        )
        return CmdResult.ok(value=total_hits)

    # Single-file mode.
    path = _resolve_script(ctx, name)
    if path is None:
        return CmdResult.fail(msg=f"Script not found: {name}")
    hits = _scan_one_file(ctx, path, prefix, fix_mode)
    return CmdResult.ok(value=hits)


def _run_long_help(ctx: PluginContext) -> str:
    """Show current legacy table + script folder count."""
    mapping_lines = [
        f"  {ctx.prefix}{old} -> {ctx.prefix}{new}" for old, new in sorted(LEGACY_COMMANDS.items())
    ]
    folder = folder_line(ctx, "run", noun="script")
    body = (
        "Scan a .run script for command names that have been renamed.\n"
        "Pass a filename to scan one file, or a name pattern (* for\n"
        "all) to scan every .run file in run/ it matches.\n"
        "Without --fix, reports the hits line by line.  With --fix,\n"
        "rewrites the file(s) in place.\n"
        "\n"
        "Current rename table:\n" + "\n".join(mapping_lines)
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
ARGS = "<filename|pattern> {--fix}"
FLAGS = {"--fix": "Rewrite the file in place instead of just reporting."}
