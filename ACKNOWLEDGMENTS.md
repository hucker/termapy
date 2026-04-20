# Acknowledgments

Termapy stands on several pieces of other people's work.  A few
deserve explicit thanks.

## reveng (Greg Cook)

Every named CRC algorithm in termapy comes from the
[**reveng CRC catalogue**](https://reveng.sourceforge.io/crc-catalogue/all.htm)
maintained by **Greg Cook** since 1999.  The reveng project documents
the polynomial, initial value, reflection, and xor-out parameters for
every standardized CRC in practical use -- 100+ algorithms, each with
a canonical `check` value over the ASCII string `"123456789"` so
implementations can be verified independently.

Without reveng, a tool like termapy would have had to reconstruct the
CRC literature from datasheets one protocol at a time.  Instead,
users get the full set correct on day one, and our test suite
verifies every algorithm against the catalogue's published check
values on every commit.

The reveng catalogue is published as reference data -- the polynomial
parameters describe well-documented public standards (ITU, NXP,
IEEE, Modbus, Bluetooth SIG, etc.) rather than original creative
work.  Termapy uses the parameter values but does not incorporate any
of reveng's own source code.  The reveng tool itself is licensed
GPLv3+; see the [reveng project page](https://reveng.sourceforge.io)
for details.

If the `/proto.crc.find` tool identifies the CRC in your packet, it's
because Greg Cook already documented it.

## pyserial

Cross-platform serial I/O is non-trivial; termapy uses
[**pyserial**](https://pyserial.readthedocs.io/) (originally by
**Chris Liechti**, now community-maintained) and inherits its
portability across Windows COM ports, macOS `/dev/cu.*` devices,
Linux `/dev/tty*` devices, and pyserial URLs (`rfc2217://`,
`loop://`, `hwgrep://`).

## Textual and Rich (Will McGugan / Textualize)

The TUI is built on [**Textual**](https://textual.textualize.io/) and
all formatted output goes through [**Rich**](https://rich.readthedocs.io/),
both by **Will McGugan** and the Textualize team.  Textual provides
the whole widget / layout / CSS / event-pump model; Rich handles
colors, markup, tables, and the screenshot rendering.  Between them,
they're what makes termapy feel modern instead of ncurses-from-1990.

## prompt_toolkit (Jonathan Slenders)

The CLI-mode REPL (history, tab completion, multi-line input,
`patch_stdout` background-thread coordination) is built on
[**prompt_toolkit**](https://python-prompt-toolkit.readthedocs.io/)
by **Jonathan Slenders**.  Every long-running command in CLI mode
renders correctly above the prompt because prompt_toolkit handles
that for us.

## Others

- [**zensical**](https://github.com/hucker/zensical) generates the
  HTML help site from the same Markdown files shipped with the app.
- The demo device nickname [BASSOMATIC-77](https://en.wikipedia.org/wiki/Bass-O-Matic)
  is Dan Aykroyd's, not ours.
