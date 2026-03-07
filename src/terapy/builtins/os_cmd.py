"""Built-in plugin: run a shell command."""

import subprocess

NAME = "os"
ARGS = "<cmd>"
HELP = "Run a shell command and show output (10s timeout). e.g. !!os dir"


def handler(ctx, args):
    if not ctx.cfg.get("os_cmd_enabled"):
        ctx.write("!!os is disabled. Set os_cmd_enabled: true in config.", "red")
        return
    if not args.strip():
        ctx.write("Usage: !!os <command>", "red")
        return
    try:
        result = subprocess.run(
            args, shell=True, capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            ctx.write(line, "white")
        for line in result.stderr.splitlines():
            ctx.write(line, "red")
    except subprocess.TimeoutExpired:
        ctx.write("Command timed out (10s limit)", "red")
