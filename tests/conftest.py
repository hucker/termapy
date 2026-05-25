# Register vendored pyserial before any test imports serial.
# This mirrors what termapy/__init__.py does at app startup.
import termapy.vendor  # noqa: F401


def _fix_msys2_path_on_windows() -> None:
    """Ensure msys2's mingw64\\bin precedes Git's mingw64\\bin in PATH.

    Symptom: under Git Bash on Windows, the CRC codegen-exec tests
    silently fail -- gcc returns rc=1 with empty stderr.  The fast
    suite, lint, and ty all stay green; only the subprocess-spawning
    codegen-exec tests fall over.

    Root cause: Git Bash prepends ``C:\\Program Files\\Git\\mingw64\\bin``
    to PATH.  When pytest spawns gcc via Python subprocess, gcc finds
    its sub-tool (cc1.exe) but Windows DLL resolution loads Git's
    older libstdc++-6 / libgcc_s_seh-1 DLLs first -- which are
    incompatible with msys2's gcc 15.x.  cc1.exe fails to load with
    NT status 0xC0000139 (STATUS_ENTRYPOINT_NOT_FOUND), gcc reports
    rc=1 with no diagnostic.

    Fix: prepend C:\\msys64\\mingw64\\bin to PATH for the test
    session.  No-op on Linux/macOS (path doesn't exist) and no-op
    on Windows shells that already have it first (PowerShell, cmd
    with normal config).  Safe to do unconditionally: the path has
    the right DLLs for the gcc/g++ in that same directory.
    """
    import os
    import sys
    if sys.platform != "win32":
        return
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    # Already first?  No-op.
    if parts and os.path.normcase(parts[0]) == os.path.normcase(msys2_bin):
        return
    # Otherwise prepend (and drop any existing later occurrence to
    # keep PATH from growing every test session).
    parts = [p for p in parts if os.path.normcase(p) != os.path.normcase(msys2_bin)]
    os.environ["PATH"] = os.pathsep.join([msys2_bin] + parts)


_fix_msys2_path_on_windows()
