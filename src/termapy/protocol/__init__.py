"""Binary-protocol toolkit: format-spec parser, CRC catalog, .pro runner.

A self-contained package for parsing, applying, and verifying binary
serial protocols.  Termapy uses it for the ``/proto.*`` plugin family
and the ``/cap.*`` capture decoders; the same machinery is reusable
for any program that needs to:

  - **Parse a format-spec string** like ``Slave:H1 Func:H2 Addr:U3-4
    CRC:crc16-modbus_le`` into structured :class:`ColumnSpec` rows.
    See :func:`parse_format_spec` and :func:`apply_format`.
  - **Compute CRCs** from a curated catalog (CRC-8, CRC-16, CRC-32,
    common parameters: MODBUS, CCITT, KERMIT, XMODEM, ARC, ...).
    See :func:`get_crc_registry` and :data:`CRC_CATALOGUE`.
  - **Generate CRC code** in C / Python / Rust for embedded targets.
    See :data:`GENERATORS`, :func:`generate_c`, etc.
  - **Collect frames** from a noisy stream with framing delimiters
    and inter-frame gap detection.  See :class:`FrameCollector`.
  - **Run protocol regression tests** from a ``.pro`` file (TOML
    test cases: send bytes, await response, diff against expected).
    See :func:`run_proto_tests`.
  - **Load visualizer plugins** (custom data-presentation modules
    for hex / decoded views).  See :func:`load_visualizers_from_dir`.

Library use (no termapy-engine deps, no Textual)::

    from termapy.protocol import parse_format_spec, apply_format

    columns = parse_format_spec("Slave:H1 Func:H2 Addr:U3-4")
    rendered = apply_format(b"\\x01\\x03\\x00\\x10\\x00\\x02", columns)
    print(rendered)

    from termapy.protocol import get_crc_registry
    crc16_modbus = get_crc_registry()["crc16-modbus"].compute
    print(hex(crc16_modbus(b"\\x01\\x03\\x00\\x10\\x00\\x02")))

Files in this package:

  - ``core.py``         -- format-spec parser, apply/diff_format,
                           FrameCollector, parse_hex, response matching,
                           ColumnSpec, parse_proto_script, TestCase
  - ``crc.py``          -- CRC catalog, registry, generic_crc
  - ``crcgen/``        -- Python / C / Rust / VHDL CRC code generators
                          (one module per target language)
  - ``runner.py``       -- .pro file execution; run_proto_tests
  - ``viz.py``          -- visualizer plugin loader

What's NOT in this package (intentionally):

  - ``proto_debug.py`` lives at the top-level because it's a Textual
    ModalScreen -- UI on top of the toolkit, not part of it.  Keeping
    it out preserves this package's "no Textual deps" property and
    its standalone-library positioning.

Dependencies outside the package boundary (kept narrow):

  - ``termapy.scripting`` -- ``parse_duration`` (one-liner helper).
  - ``termapy.config``    -- ``open_serial`` (only ``runner.py``;
    needs an actual port to run regression tests).
  - ``termapy.plugins``   -- ``BoundaryException`` (only ``crc.py``
    and ``viz.py``, for plugin-loading error boundaries).

The package is positioned as a viable standalone PyPI release
(``serial-protocol-toolkit`` or similar) once the surface
stabilizes; nothing here imports termapy-engine, MCP, or Textual.
"""

from __future__ import annotations

from termapy.protocol.core import (
    DIFF_STYLES,
    ColumnSpec,
    FrameCollector,
    ProtoScript,
    Step,
    TestCase,
    apply_format,
    diff_bytes,
    diff_columns,
    extract_fmt_title,
    format_diff_markup,
    format_hex,
    format_hex_dump,
    format_smart,
    format_spaced,
    load_proto_script,
    match_response,
    overflow_count,
    parse_data,
    parse_data_segments,
    parse_format_spec,
    parse_hex,
    parse_pattern,
    parse_proto_script,
    parse_toml_script,
    strip_ansi,
)
from termapy.protocol.crc import (
    CRC_CATALOGUE,
    CrcAlgorithm,
    builtins_crc_dir,
    get_crc_registry,
    load_crc_plugins,
    reset_crc_registry,
)
from crcglot import (
    LANGUAGES,
    generate_c,
    generate_c_from_entry,
    generate_python,
    generate_python_from_entry,
    generate_rust,
    generate_rust_from_entry,
    generate_vhdl,
    generate_vhdl_from_entry,
)
from termapy.protocol.runner import (
    expand_result_template,
    run_proto_tests,
)
from termapy.protocol.viz import (
    VisualizerInfo,
    builtins_viz_dir,
    load_visualizers_from_dir,
)

# crcglot 0.8.0 dropped the module-level GENERATORS / GENERATORS_FROM_ENTRY
# dicts in favor of LANGUAGES[code].generator.  termapy's proto.py
# dispatcher keys generators by language code, so rebuild the dicts here.
# This also automatically picks up languages crcglot adds (csharp, go,
# typescript, verilog in 0.8.0) -- though termapy only registers REPL
# commands for the subset it knows about; extra GENERATORS keys are
# harmless (never dispatched to).
GENERATORS = {code: info.generator for code, info in LANGUAGES.items()}
GENERATORS_FROM_ENTRY = {
    code: info.generator_from_entry for code, info in LANGUAGES.items()
}


__all__ = [
    # core: format spec + parse/apply
    "DIFF_STYLES",
    "ColumnSpec",
    "FrameCollector",
    "ProtoScript",
    "Step",
    "TestCase",
    "apply_format",
    "diff_bytes",
    "diff_columns",
    "extract_fmt_title",
    "format_diff_markup",
    "format_hex",
    "format_hex_dump",
    "format_smart",
    "format_spaced",
    "load_proto_script",
    "match_response",
    "overflow_count",
    "parse_data",
    "parse_data_segments",
    "parse_format_spec",
    "parse_hex",
    "parse_pattern",
    "parse_proto_script",
    "parse_toml_script",
    "strip_ansi",
    # crc
    "CRC_CATALOGUE",
    "CrcAlgorithm",
    "builtins_crc_dir",
    "get_crc_registry",
    "load_crc_plugins",
    "reset_crc_registry",
    # crcgen
    "GENERATORS",
    "GENERATORS_FROM_ENTRY",
    "generate_c",
    "generate_c_from_entry",
    "generate_python",
    "generate_python_from_entry",
    "generate_rust",
    "generate_rust_from_entry",
    "generate_vhdl",
    "generate_vhdl_from_entry",
    # runner
    "expand_result_template",
    "run_proto_tests",
    # viz
    "VisualizerInfo",
    "builtins_viz_dir",
    "load_visualizers_from_dir",
]
