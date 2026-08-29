"""The dependency and attribution registry -- one table, every consumer iterates it.

Termapy credits the projects it is built on in three places: the Help
button's tooltip, the ``/credits`` command, and the acknowledgments help
page.  Each used to be a hand-kept list, and they drifted (``frist`` was
a runtime dependency for a release before any of them named it).  This
module is the single source: ``CREDITS`` holds one record per dependency,
and

- ``help_tooltip.build_help_tooltip`` renders the records flagged
  ``in_tooltip``;
- ``scripts/sync_acknowledgments.py`` regenerates the "Other runtime
  dependencies" and "Optional extras" sections of
  ``help/acknowledgments.md`` from the ``runtime`` / ``optional`` records
  (between the ``deps:start`` / ``deps:end`` markers), then embeds the
  page into ``credits.py`` as before;
- ``tests/test_credits_data.py`` cross-checks the table against
  ``pyproject.toml`` in both directions and against the vendored tree,
  so a dependency added without a credit fails the suite.

Pure data, no Textual, no pyserial.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["runtime", "optional", "vendored", "reference", "tooling"]


@dataclass(frozen=True)
class Credit:
    """One dependency or attribution.

    Attributes:
        package: Distribution name exactly as it appears in ``pyproject.toml``
            (``runtime`` / ``optional``), the folder or module name under
            ``vendor/`` (``vendored``), or a display name (``reference`` /
            ``tooling``).
        role: Short role for the tooltip grid ("serial I/O", "CRC engine").
        author: People or organization to credit.
        url: Project home.
        note: One or two sentences for the acknowledgments bullet.  Empty
            means the page credits this one in prose of its own (Textual,
            prompt_toolkit), so the generator emits no bullet for it.
        kind: ``runtime`` = ``[project].dependencies``; ``optional`` = an
            extra; ``vendored`` = shipped under ``src/termapy/vendor/``;
            ``reference`` = data, not code (the reveng catalog);
            ``tooling`` = build/docs only.
        covers: Further distribution names this record accounts for (the
            tree-sitter grammars ride with ``tree-sitter``).
        extra: For ``optional``, the extra's name in ``pyproject.toml``.
        in_tooltip: Show in the Help tooltip's "Built on open source" grid.
        label: Tooltip display name when it differs from ``package``.
        role_detail: Dim second line under ``role`` in the tooltip (the
            reveng URL sits under "CRC algorithms").
    """

    package: str
    role: str
    author: str
    url: str
    note: str = ""
    kind: Kind = "runtime"
    covers: tuple[str, ...] = ()
    extra: str = ""
    in_tooltip: bool = False
    label: str = ""
    role_detail: str = ""

    @property
    def display_name(self) -> str:
        """Name shown in the tooltip grid."""
        return self.label or self.package

    @property
    def names(self) -> tuple[str, ...]:
        """Every distribution name this record accounts for."""
        return (self.package, *self.covers)


# Order is display order: the tooltip grid and the generated bullet lists
# follow it.  crcglot and the reveng catalog stay adjacent on purpose --
# the engine is built on the catalog, and dropping either erases a link.
CREDITS: tuple[Credit, ...] = (
    Credit(
        "pyserial", "serial I/O", "Chris Liechti", "https://github.com/pyserial/pyserial",
        kind="vendored", in_tooltip=True,
    ),
    # Textual and prompt_toolkit have their own prose sections on the
    # acknowledgments page, so they carry no note (no generated bullet).
    Credit(
        "textual", "TUI + output", "Will McGugan", "https://textual.textualize.io/",
        in_tooltip=True, label="Textual / Rich",
    ),
    Credit(
        "prompt-toolkit", "CLI", "Jonathan Slenders",
        "https://python-prompt-toolkit.readthedocs.io/",
        in_tooltip=True, label="prompt_toolkit",
    ),
    Credit(
        "pygments", "syntax highlighting", "Georg Brandl and the Pygments team",
        "https://pygments.org/",
        note="syntax highlighting for the in-app TextArea editors (config JSON, "
             "proto TOML, script bash).",
    ),
    Credit(
        "tree-sitter", "editor parsers", "Max Brunsfeld; grammars community-maintained",
        "https://tree-sitter.github.io/",
        note="plus the JSON / TOML / bash grammars: the incremental parsers powering "
             "the same editors.",
        covers=("tree-sitter-json", "tree-sitter-toml", "tree-sitter-bash"),
    ),
    Credit(
        "packaging", "version parsing", "Python Packaging Authority (PyPA)",
        "https://packaging.pypa.io/",
        note="PEP 440 version comparison for the update check, which compares the "
             "installed termapy against the latest PyPI release.",
    ),
    Credit(
        "platformdirs", "user directories", "the platformdirs maintainers",
        "https://github.com/platformdirs/platformdirs",
        note="cross-platform user state / config directory resolution "
             "(community fork of the original appdirs by ActiveState).",
    ),
    Credit(
        "crcglot", "CRC engine", "Chuck Bass", "https://github.com/hucker/crcglot",
        note="the reveng CRC catalog, detection / reversal, and multi-language code "
             "generation behind every /proto.crc.* command; extracted from termapy "
             "into its own package.",
        in_tooltip=True,
    ),
    Credit(
        "reveng catalog", "CRC algorithms", "Greg Cook",
        "https://reveng.sourceforge.io/crc-catalogue/all.htm",
        kind="reference", in_tooltip=True, role_detail="reveng.sourceforge.io",
    ),
    Credit(
        "frist", "ages, durations", "Chuck Bass", "https://github.com/hucker/frist",
        note='the age and duration engine behind every "10 min ago" and "1.5s" '
             "termapy prints: unit selection and calendar-accurate months, so "
             "termapy owns only the labels.",
        in_tooltip=True,
    ),
    Credit(
        "xmodem", "file transfer", "Wijnand Modderman, Jeff Quast, Andrew Leech",
        "https://github.com/tehmaze/xmodem", kind="vendored", in_tooltip=True,
    ),
    Credit(
        "ymodem", "file transfer", "alexwoo", "https://github.com/alexwoo1900/ymodem",
        kind="vendored", in_tooltip=True,
    ),
    Credit(
        "mcp", "MCP server", "Anthropic", "https://github.com/modelcontextprotocol/python-sdk",
        note="the Model Context Protocol SDK that termapy --mcp is built on.",
        kind="optional", extra="mcp",
    ),
    Credit(
        "textual-serve", "browser serving", "Textualize",
        "https://github.com/Textualize/textual-serve",
        note="serves the TUI to a browser for termapy --web.",
        kind="optional", extra="web",
    ),
    Credit(
        "zensical", "docs build", "Chuck Bass", "https://github.com/hucker/zensical",
        note="generates the HTML help site from the same Markdown files shipped "
             "with the app.",
        kind="tooling",
    ),
)


def credits_of(kind: Kind) -> tuple[Credit, ...]:
    """The records of one kind, in display order."""
    return tuple(credit for credit in CREDITS if credit.kind == kind)


def tooltip_credits() -> tuple[Credit, ...]:
    """The records shown in the Help tooltip grid, in display order."""
    return tuple(credit for credit in CREDITS if credit.in_tooltip)
