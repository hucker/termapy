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

## Other runtime dependencies

Termapy would not exist without a long tail of packages from PyPI.
Each one below is a direct runtime dependency declared in
``pyproject.toml``:

- [**pygments**](https://pygments.org/) -- syntax highlighting for
  the in-app TextArea editors (config JSON, proto TOML, script
  bash).  Started by **Georg Brandl** and now maintained by the
  Pygments team.
- [**tree-sitter**](https://tree-sitter.github.io/) + the JSON /
  TOML / bash grammars -- incremental parsers powering the same
  editors.  Tree-sitter itself was started by **Max Brunsfeld** at
  GitHub; the grammar packages are community-maintained.
- [**packaging**](https://packaging.pypa.io/) -- PEP 440 version
  comparison used by ``/include`` for device command help JSON
  versioning.  Maintained by the Python Packaging Authority (PyPA).
- [**platformdirs**](https://github.com/platformdirs/platformdirs)
  -- cross-platform user state / config directory resolution
  (community fork of the original ``appdirs`` by ActiveState).

## Vendored code

Three packages are vendored under ``src/termapy/vendor/`` rather
than being runtime dependencies -- see
[vendor/LICENSES.md](src/termapy/vendor/LICENSES.md) for versions
and licenses:

- **pyserial** -- vendored because upstream hasn't cut a release
  since November 2020 and we need fixes that only exist in the
  tip.  Chris Liechti (above).
- **xmodem** by **Wijnand Modderman**, **Jeff Quast**, and **Andrew
  Leech** -- the classic XMODEM / XMODEM-CRC / XMODEM-1K protocol
  implementation.
- **ymodem** by **alexwoo** -- the companion YMODEM batch
  implementation (``ordered-set`` dependency replaced with built-in
  ``set()``).

## Docs and demo

- [**zensical**](https://github.com/hucker/zensical) generates the
  HTML help site from the same Markdown files shipped with the app.
- The demo device nickname [BASSOMATIC-77](https://en.wikipedia.org/wiki/Bass-O-Matic)
  is Dan Aykroyd's, not ours.
