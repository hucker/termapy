"""Execution-verified tests for the C and Rust CRC code generators.

The structural tests in ``test_crc_codegen.py`` only check string
presence -- they catch "did the generator emit the right shape" but
NOT "does the generated code produce the right value."  These tests
fill that gap by actually compiling and running each generated
implementation against the canonical reveng ``check`` value
(``b"123456789"`` -> ``entry["check"]``), parametrized over every
algorithm in ``CRC_CATALOGUE``.

Toolchain gating: the C tests skip when ``gcc`` is not in PATH; the
Rust tests skip when ``rustc`` is not in PATH.  This keeps the fast
suite green on machines without a C/Rust toolchain (and on Windows
CI runners without one) while giving full execution verification on
developer machines and Linux CI where the toolchains are available.

Python verification lives in ``test_crc_codegen.py`` because
exec-ing a Python module needs no external toolchain.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import CRC_CATALOGUE, generate_c, generate_rust, generate_vhdl


# Every test in this file shells out to a compiler / simulator
# (gcc / rustc / ghdl) and is therefore subprocess-spawning.  Per
# CLAUDE.md's testing conventions the "slow" marker covers exactly
# that class of test, so apply it module-wide -- the full suite
# still runs them, but ``pytest -m "not slow"`` keeps the fast
# iteration loop at its baseline ~35s instead of ~215s.
pytestmark = pytest.mark.slow


HAS_GCC = shutil.which("gcc") is not None
HAS_RUSTC = shutil.which("rustc") is not None
HAS_GHDL = shutil.which("ghdl") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCExecutes:
    """Compile each generated C pair + a synthesized runner, then run.

    Per-algorithm flow:

    1. ``generate_c(name)`` -> ``(header, source)`` (a ``.h`` + ``.c`` pair)
    2. Write both to a fresh tmp dir under their conventional names
       (``<fname>.h`` and ``<fname>.c``).
    3. Synthesize a tiny ``runner.c`` that ``#include``s the header
       and has a ``main()`` that returns the result of the generated
       ``<fname>_self_test()``.
    4. ``gcc <fname>.c runner.c -o run`` -- compile-error caught
       here is a generator bug (syntactically broken C).
    5. Execute the binary -- nonzero exit means the generated function
       produced the wrong CRC for ``"123456789"``.

    The whole loop runs in ~80 ms per algorithm (~5 s for all 64) on
    a modern laptop with gcc on PATH.
    """

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_returns_zero(self, name, tmp_path):
        # Arrange
        result = generate_c(name)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        fname = _func_name(name)

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{ return {fname}_self_test(); }}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99",
                "-Wall",
                "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: gcc failed: {compile_result.stderr.decode(errors='replace')}"
        )

        # Act -- run
        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: self_test returned {run_result.returncode} "
            f"(expected 0 == check value matches reveng)"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_self_test_returns_zero(self, name, tmp_path):
        """Same as above but with table=True (table-driven implementation)."""
        # Arrange
        result = generate_c(name, table=True)
        assert result is not None, f"generate_c({name!r}, table=True) returned None"
        header, source = result
        fname = _func_name(name)

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{ return {fname}_self_test(); }}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99",
                "-Wall",
                "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table): gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table): self_test returned {run_result.returncode}"
        )


@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustExecutes:
    """Compile each generated .rs file as a test binary, then run.

    The generator embeds a ``#[cfg(test)] mod tests`` block with one
    ``#[test] fn check_value_matches_reveng()`` assertion per file,
    so ``rustc --test file.rs`` produces a binary that runs that
    test on execution.  Nonzero exit = test failed = generator
    produced wrong CRC for ``b"123456789"``.
    """

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_check_value_matches_reveng(self, name, tmp_path):
        # Arrange
        code = generate_rust(name)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)

        src = tmp_path / f"{fname}.rs"
        src.write_text(code)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile as a test harness
        compile_result = subprocess.run(
            [
                "rustc",
                "--test",
                "--edition=2021",
                "-o", str(binary),
                str(src),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        # Act -- run (rustc --test binary returns 0 on all tests pass)
        run_result = subprocess.run([str(binary)], capture_output=True, cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: test binary returned {run_result.returncode}: "
            f"{run_result.stdout.decode(errors='replace')}"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_check_value_matches_reveng(self, name, tmp_path):
        """Same as above but with table=True."""
        # Arrange
        code = generate_rust(name, table=True)
        assert code is not None, (
            f"generate_rust({name!r}, table=True) returned None"
        )
        fname = _func_name(name)

        src = tmp_path / f"{fname}.rs"
        src.write_text(code)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "rustc",
                "--test",
                "--edition=2021",
                "-o", str(binary),
                str(src),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table): rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run([str(binary)], capture_output=True, cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table): test binary returned {run_result.returncode}"
        )


@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
class TestGeneratedVhdlExecutes:
    """Compile each generated .vhd through GHDL + a synthesized testbench.

    Per-algorithm flow:

    1. ``generate_vhdl(name)`` -> ``.vhd`` source (a package containing
       the compute function and a ``_self_test`` boolean function).
    2. Write the package to ``<fname>.vhd``.
    3. Synthesize a tiny ``<fname>_tb.vhd`` whose architecture is a
       single process that ``assert``s ``<fname>_self_test`` with
       ``severity failure`` -- GHDL halts the simulation with a
       non-zero exit on assertion failure.
    4. ``ghdl -a <fname>.vhd <fname>_tb.vhd`` -- analyze both files.
       Syntax errors in the generator output surface here.
    5. ``ghdl -e <fname>_tb`` -- elaborate the testbench entity.
    6. ``ghdl -r <fname>_tb`` -- run the simulation.  Exit 0 = check
       value matches; non-zero = the generated function produced the
       wrong CRC for "123456789".

    GHDL is available via ``apt install ghdl`` on Linux, ``brew
    install ghdl`` on macOS, and a manual installer on Windows.  Not
    preinstalled on GitHub Actions runners, so the suite stays green
    on CI without it.
    """

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_passes(self, name, tmp_path):
        # Arrange
        code = generate_vhdl(name)
        assert code is not None, f"generate_vhdl({name!r}) returned None"
        fname = _func_name(name)

        (tmp_path / f"{fname}.vhd").write_text(code)
        (tmp_path / f"{fname}_tb.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            f"use work.{fname}_pkg.all;\n"
            "\n"
            f"entity {fname}_tb is\n"
            "end entity;\n"
            "\n"
            f"architecture sim of {fname}_tb is\n"
            "begin\n"
            "    process\n"
            "    begin\n"
            f"        assert {fname}_self_test\n"
            f'            report "{name} self_test FAILED"\n'
            "            severity failure;\n"
            "        wait;\n"
            "    end process;\n"
            "end architecture;\n"
        )

        # Act -- analyze (compile to GHDL's working library)
        analyze = subprocess.run(
            ["ghdl", "-a", "--std=08", f"{fname}.vhd", f"{fname}_tb.vhd"],
            capture_output=True,
            cwd=tmp_path,
        )
        assert analyze.returncode == 0, (
            f"{name}: ghdl analyze failed: "
            f"{analyze.stderr.decode(errors='replace')}"
        )

        # Act -- elaborate the testbench entity
        elaborate = subprocess.run(
            ["ghdl", "-e", "--std=08", f"{fname}_tb"],
            capture_output=True,
            cwd=tmp_path,
        )
        assert elaborate.returncode == 0, (
            f"{name}: ghdl elaborate failed: "
            f"{elaborate.stderr.decode(errors='replace')}"
        )

        # Act -- run the simulation
        run = subprocess.run(
            ["ghdl", "-r", "--std=08", f"{fname}_tb"],
            capture_output=True,
            cwd=tmp_path,
        )

        # Assert
        assert run.returncode == 0, (
            f"{name}: ghdl simulation returned {run.returncode}: "
            f"{run.stderr.decode(errors='replace')}"
        )


# ─────────────────────────────────────────────────────────────────────
# Streaming API tests -- splittability invariant.
#
# For each algorithm in each target language, verify that the streaming
# primitives (init / update / finalize) produce the reveng check value
# across three input-splitting patterns:
#
#   1. Split mid-input: init -> update("1234") -> update("56789")
#   2. Empty chunk first: init -> update("") -> update("123456789")
#   3. Empty chunk last:  init -> update("123456789") -> update("")
#
# All three must equal the reveng catalogue's check value.  A failure
# in any pattern flags a streaming bug (wrong state shape, broken
# update accumulator, finalize logic accidentally re-applied in update,
# zero-length input mishandled).
# ─────────────────────────────────────────────────────────────────────


def _c_state_type(width: int) -> str:
    """Pick the C state type to match what generate_c uses internally."""
    if width <= 8:
        return "uint8_t"
    if width <= 16:
        return "uint16_t"
    if width <= 32:
        return "uint32_t"
    return "uint64_t"


def _rust_state_type(width: int) -> str:
    """Pick the Rust state type to match what generate_rust uses internally."""
    if width <= 8:
        return "u8"
    if width <= 16:
        return "u16"
    if width <= 32:
        return "u32"
    return "u64"


@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCStreaming:
    """Verify the C streaming triple (init / update / finalize) satisfies
    the splittability invariant against the reveng check value."""

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        result = generate_c(name, table=table)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        fname = _func_name(name)
        ctype = _c_state_type(entry["width"])

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{\n"
            f"    /* Pattern 1: split at byte 4 */\n"
            f"    {ctype} s1 = {fname}_init();\n"
            f'    s1 = {fname}_update(s1, (const uint8_t *)"1234", 4);\n'
            f'    s1 = {fname}_update(s1, (const uint8_t *)"56789", 5);\n'
            f"    if ({fname}_finalize(s1) != 0x{expected:X}) return 1;\n"
            f"    /* Pattern 2: empty chunk first */\n"
            f"    {ctype} s2 = {fname}_init();\n"
            f'    s2 = {fname}_update(s2, (const uint8_t *)"", 0);\n'
            f'    s2 = {fname}_update(s2, (const uint8_t *)"123456789", 9);\n'
            f"    if ({fname}_finalize(s2) != 0x{expected:X}) return 2;\n"
            f"    /* Pattern 3: empty chunk last */\n"
            f"    {ctype} s3 = {fname}_init();\n"
            f'    s3 = {fname}_update(s3, (const uint8_t *)"123456789", 9);\n'
            f'    s3 = {fname}_update(s3, (const uint8_t *)"", 0);\n'
            f"    if ({fname}_finalize(s3) != 0x{expected:X}) return 3;\n"
            f"    return 0;\n"
            f"}}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99", "-Wall", "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table={table}): gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table={table}): streaming returned "
            f"{run_result.returncode} (1=split, 2=empty-first, 3=empty-last)"
        )


@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustStreaming:
    """Verify the Rust streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_rust(name, table=table)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)
        rtype = _rust_state_type(entry["width"])

        # Append a main() that exercises the streaming patterns.  rustc
        # without --test happily compiles a .rs with both fn definitions
        # and main(); the existing #[cfg(test)] mod stays inert.
        main_src = (
            f"\nfn main() {{\n"
            f"    let s1 = {fname}_init();\n"
            f'    let s1 = {fname}_update(s1, b"1234");\n'
            f'    let s1 = {fname}_update(s1, b"56789");\n'
            f"    if {fname}_finalize(s1) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(1); }}\n"
            f"    let s2 = {fname}_init();\n"
            f'    let s2 = {fname}_update(s2, b"");\n'
            f'    let s2 = {fname}_update(s2, b"123456789");\n'
            f"    if {fname}_finalize(s2) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(2); }}\n"
            f"    let s3 = {fname}_init();\n"
            f'    let s3 = {fname}_update(s3, b"123456789");\n'
            f'    let s3 = {fname}_update(s3, b"");\n'
            f"    if {fname}_finalize(s3) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(3); }}\n"
            f"}}\n"
        )

        src = tmp_path / f"{fname}.rs"
        src.write_text(code + main_src)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile (NOT --test; we have our own main() now)
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(src)],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table={table}): rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table={table}): streaming returned "
            f"{run_result.returncode} (1=split, 2=empty-first, 3=empty-last)"
        )


# ─────────────────────────────────────────────────────────────────────
# Slice-by-8 equivalence tests.
#
# Slice-by-8 is a high-throughput CRC optimization (8 tables, 8 bytes
# per iteration -- 5-10x faster than plain table-driven on large
# buffers).  Verification strategy: generate BOTH bit-by-bit and
# slice-by-8 in the target language under different symbol names,
# compile both into the same runner, and assert they produce identical
# output across a range of input lengths.  Since the bit-by-bit form
# is already reveng-verified (by the TestGenerated{C,Rust}Executes
# tests above), equivalence proves slice-by-8 is correct.
#
# Input lengths chosen to exercise the 8-byte main loop AND the 1-7
# byte tail loop: 0 (degenerate), 1 (pure tail), 7 (just under one
# chunk), 8 (exactly one chunk), 9 (one chunk + 1-byte tail), 15
# (just under two chunks), 16 (exactly two chunks), 100 (12 chunks +
# 4-byte tail).  The input data is the cyclic byte sequence
# 0x00, 0x01, ..., 0xFF, ... to avoid all-zero or all-one degenerate
# patterns that might mask indexing bugs.
#
# Limited to CRC-32 and CRC-64 algorithms; slice-by-8 only makes sense
# at those widths (validated by the slice8=True ValueError in the
# generators).
# ─────────────────────────────────────────────────────────────────────


# Input lengths spanning degenerate, sub-chunk, exact-chunk, mixed.
_SLICE8_INPUT_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(
        n for n, e in CRC_CATALOGUE.items() if e["width"] in (32, 64)
    )


@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated C."""

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two C pairs with disjoint symbol names so
        # they can link in the same runner.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_header, bb_source = generate_c(name, symbol=bb_sym)
        s8_header, s8_source = generate_c(name, slice8=True, symbol=s8_sym)
        ctype = _c_state_type(CRC_CATALOGUE[name]["width"])

        (tmp_path / f"{bb_sym}.h").write_text(bb_header)
        (tmp_path / f"{bb_sym}.c").write_text(bb_source)
        (tmp_path / f"{s8_sym}.h").write_text(s8_header)
        (tmp_path / f"{s8_sym}.c").write_text(s8_source)

        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner_src = (
            f'#include "{bb_sym}.h"\n'
            f'#include "{s8_sym}.h"\n'
            f"#include <stdio.h>\n"
            f"int main(void) {{\n"
            f"    static uint8_t buf[256];\n"
            f"    for (int k = 0; k < 256; k++) buf[k] = (uint8_t)k;\n"
            f"    size_t lengths[] = {{ {lengths_csv} }};\n"
            f"    size_t nlen = sizeof(lengths) / sizeof(lengths[0]);\n"
            f"    for (size_t li = 0; li < nlen; li++) {{\n"
            f"        size_t n = lengths[li];\n"
            f"        {ctype} bb = {bb_sym}(buf, n);\n"
            f"        {ctype} s8 = {s8_sym}(buf, n);\n"
            f"        if (bb != s8) {{\n"
            f'            fprintf(stderr, "len=%zu bb=0x%llx s8=0x%llx\\n",\n'
            f"                    n, (unsigned long long)bb,\n"
            f"                    (unsigned long long)s8);\n"
            f"            return (int)(li + 1);\n"
            f"        }}\n"
            f"    }}\n"
            f"    return 0;\n"
            f"}}\n"
        )
        (tmp_path / "runner.c").write_text(runner_src)

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99", "-Wall", "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{bb_sym}.c"),
                str(tmp_path / f"{s8_sym}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run(
            [str(binary)], capture_output=True, cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # nonzero index identifies which length disagreed.
        assert run_result.returncode == 0, (
            f"{name}: slice8 != bit-by-bit at length "
            f"{_SLICE8_INPUT_LENGTHS[run_result.returncode - 1]}: "
            f"{run_result.stderr.decode(errors='replace')}"
        )


@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated Rust."""

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two .rs files with disjoint symbol names.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_rust(name, symbol=bb_sym)
        s8_code = generate_rust(name, slice8=True, symbol=s8_sym)
        rtype = _rust_state_type(CRC_CATALOGUE[name]["width"])

        bb_path = tmp_path / f"{bb_sym}.rs"
        s8_path = tmp_path / f"{s8_sym}.rs"
        bb_path.write_text(bb_code)
        s8_path.write_text(s8_code)

        # The two files define identically-named CRC_TABLE /
        # CRC_SLICE_TABLES constants but in disjoint modules, so we
        # ``include!`` them into separate mods to avoid name collisions.
        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner_src = (
            f'mod bb {{ include!("{bb_sym}.rs"); }}\n'
            f'mod s8 {{ include!("{s8_sym}.rs"); }}\n'
            f"fn main() {{\n"
            f"    let mut buf = [0u8; 256];\n"
            f"    for k in 0..256 {{ buf[k] = k as u8; }}\n"
            f"    let lengths: [usize; {len(_SLICE8_INPUT_LENGTHS)}] = "
            f"[{lengths_csv}];\n"
            f"    for (li, &n) in lengths.iter().enumerate() {{\n"
            f"        let bb: {rtype} = bb::{bb_sym}(&buf[..n]);\n"
            f"        let s8: {rtype} = s8::{s8_sym}(&buf[..n]);\n"
            f"        if bb != s8 {{\n"
            f'            eprintln!("len={{}} bb=0x{{:x}} s8=0x{{:x}}", '
            f"n, bb, s8);\n"
            f"            std::process::exit((li + 1) as i32);\n"
            f"        }}\n"
            f"    }}\n"
            f"}}\n"
        )
        runner_path = tmp_path / "runner.rs"
        runner_path.write_text(runner_src)

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run.  -A warnings silences unused-function
        # / unused-const warnings from the included bit-by-bit + slice-by-8
        # files (each contains a _self_test mod we don't invoke from main).
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(runner_path)],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run(
            [str(binary)], capture_output=True, cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length.
        assert run_result.returncode == 0, (
            f"{name}: slice8 != bit-by-bit at length "
            f"{_SLICE8_INPUT_LENGTHS[run_result.returncode - 1]}: "
            f"{run_result.stderr.decode(errors='replace')}"
        )


@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
class TestGeneratedVhdlStreaming:
    """Verify the VHDL streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        w = entry["width"]
        expected = entry["check"]
        code = generate_vhdl(name)
        assert code is not None, f"generate_vhdl({name!r}) returned None"
        fname = _func_name(name)

        # Format the expected check value as a VHDL literal matching
        # the package's convention (hex bit-string for multiple-of-4
        # widths, to_unsigned otherwise).
        if w % 4 == 0:
            expected_lit = f'x"{expected:0{w // 4}X}"'
        else:
            expected_lit = f"std_logic_vector(to_unsigned({expected}, {w}))"

        (tmp_path / f"{fname}.vhd").write_text(code)
        (tmp_path / f"{fname}_tb.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            "use ieee.numeric_std.all;\n"
            f"use work.{fname}_pkg.all;\n"
            "\n"
            f"entity {fname}_tb is\n"
            "end entity;\n"
            "\n"
            f"architecture sim of {fname}_tb is\n"
            "begin\n"
            "    process\n"
            f"        variable s: std_logic_vector({w - 1} downto 0);\n"
            "    begin\n"
            "        -- Pattern 1: split at byte 4\n"
            f"        s := {fname}_init;\n"
            f'        s := {fname}_update(s, x"31323334");\n'
            f'        s := {fname}_update(s, x"3536373839");\n'
            f"        assert {fname}_finalize(s) = {expected_lit}\n"
            f'            report "{name} split-streaming FAILED"\n'
            "            severity failure;\n"
            "        -- Pattern 2: empty chunk first, then full\n"
            f"        s := {fname}_init;\n"
            f'        s := {fname}_update(s, x"313233343536373839");\n'
            f"        assert {fname}_finalize(s) = {expected_lit}\n"
            f'            report "{name} full-update FAILED"\n'
            "            severity failure;\n"
            "        wait;\n"
            "    end process;\n"
            "end architecture;\n"
        )

        # Act -- analyze, elaborate, run
        for stage, args in [
            ("analyze", ["ghdl", "-a", "--std=08", f"{fname}.vhd", f"{fname}_tb.vhd"]),
            ("elaborate", ["ghdl", "-e", "--std=08", f"{fname}_tb"]),
            ("run", ["ghdl", "-r", "--std=08", f"{fname}_tb"]),
        ]:
            result = subprocess.run(args, capture_output=True, cwd=tmp_path)
            assert result.returncode == 0, (
                f"{name}: ghdl {stage} failed: "
                f"{result.stderr.decode(errors='replace')}"
            )
