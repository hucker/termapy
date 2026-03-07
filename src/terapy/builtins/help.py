"""Built-in plugin: list commands or show help for a specific command."""

NAME = "help"
ARGS = "{cmd}"
HELP = "List REPL commands, or show help for one command."


def handler(ctx, args):
    name = args.strip().lower() if isinstance(args, str) else ""
    prefix = ctx.engine.prefix
    if name:
        plugin = ctx.engine.plugins.get(name)
        if not plugin:
            ctx.write(f"Unknown command: {name}", "red")
            return
        arg_str = f" {plugin.args}" if plugin.args else ""
        ctx.write(f"{prefix}{name}{arg_str} — {plugin.help}")
        if plugin.source != "built-in":
            ctx.write(f"  (source: {plugin.source})", "dim")
    else:
        groups = {}
        for cmd_name, plugin in ctx.engine.plugins.items():
            groups.setdefault(plugin.source, []).append((cmd_name, plugin))

        # Measure columns: cmd width and args width across ALL plugins
        all_plugins = list(ctx.engine.plugins.values())
        cmd_w = max(len(prefix) + len(p.name) for p in all_plugins) + 2
        arg_w = max((len(p.args) for p in all_plugins if p.args), default=0) + 2

        for source, plugins in groups.items():
            ctx.write(f"── {source} ──")
            for cmd_name, plugin in plugins:
                cmd_col = f"  {prefix}{cmd_name}".ljust(cmd_w + 2)
                arg_col = (plugin.args or "").ljust(arg_w)
                ctx.write(f"{cmd_col}{arg_col}  {plugin.help}")
