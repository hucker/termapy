#!/bin/sh
# git textconv for *.py: show a file the way ruff formats it, so a diff
# across the 2026-10-17 formatting commit -- or against any branch older
# than it -- shows real changes only, not layout.
#
# Opt in per command (it never runs on a plain `git diff`):
#   git -c diff.ruff-format.textconv="sh scripts/format_textconv.sh" diff <old> <new>
# or once per clone, as `git fdiff <old> <new>`:
#   git config alias.fdiff '!git -c diff.ruff-format.textconv="sh scripts/format_textconv.sh" diff'
#
# .gitattributes maps *.py to the ruff-format diff driver; git runs this
# from the top of the work tree with the file to show as $1.  The project's
# pyproject.toml settings apply (git hands over a temp copy, outside the
# tree, so the config is named rather than discovered).  A file ruff cannot
# parse is shown as it is.  Two passes, because ruff's layout can still move
# on a second pass (it did for two files, 2026-10-17), and the tree holds the
# settled layout.
fmt() { uv run --no-sync ruff format --config pyproject.toml --stdin-filename "$1" - 2>/dev/null; }
once=$(fmt "$1" < "$1") || { cat "$1"; exit 0; }
printf '%s\n' "$once" | fmt "$1" || printf '%s\n' "$once"
