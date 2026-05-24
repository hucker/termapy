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

from termapy.protocol import CRC_CATALOGUE, generate_c, generate_rust, generate_vhdl


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
