"""Built-in plugin: show file contents."""

from pathlib import Path

NAME = "show"
ARGS = "<name>"
HELP = "Show a file. $cfg for current config, or a filename."

_SHOW_SPECIAL = {"$cfg"}


def handler(ctx, args):
    name = args.strip()
    if not name:
        ctx.write("Usage: !!show <name>  ($cfg for config, or a filename)", "red")
        return
    key = name.lower()
    if key == "$cfg":
        if not ctx.config_path:
            ctx.write("No config file loaded.", "red")
            return
        path = Path(ctx.config_path)
    elif name.startswith("$"):
        known = ", ".join(sorted(_SHOW_SPECIAL))
        ctx.write(f"Unknown special name: {name}. Known: {known}", "red")
        return
    else:
        path = Path(name)
    if not path.exists():
        ctx.write(f"File not found: {path}", "red")
        return
    try:
        text = path.read_text(encoding="utf-8")
        ctx.write(f"--- {path} ---")
        for line in text.splitlines():
            ctx.write(line)
        ctx.write("--- end ---")
    except Exception as e:
        ctx.write(f"Error reading {path}: {e}", "red")
