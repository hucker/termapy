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
    NT status 0xC0000139 (STATUS_ENTRYPOINT_NOT_FOUND); gcc reports
    rc=1 with no diagnostic.

    Fix: prepend C:\\msys64\\mingw64\\bin to PATH for the test
    session, AND warn so the user knows it happened (silent fixes
    hide reality -- the user might wonder why their other tooling
    sees one gcc and pytest sees another).  No-op on Linux/macOS
    (msys2 path doesn't exist) and no-op on Windows shells where
    msys2 is already first (PowerShell, cmd with normal config).
    """
    import os
    import sys
    import warnings
    if sys.platform != "win32":
        return
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(part) for part in parts]
    norm_msys2 = os.path.normcase(msys2_bin)
    # Already first?  No-op.
    if norm and norm[0] == norm_msys2:
        return
    # Detect the specific bad condition: Git's mingw64\bin appears
    # in PATH ahead of msys2's.  Worth a loud warning in that case
    # because (a) the user's interactive gcc and pytest's gcc would
    # resolve to different installs, and (b) a permanent fix is a
    # one-line .bashrc edit.
    git_norm = os.path.normcase(r"C:\Program Files\Git\mingw64\bin")
    git_idx = norm.index(git_norm) if git_norm in norm else -1
    msys2_idx = norm.index(norm_msys2) if norm_msys2 in norm else -1
    if git_idx >= 0 and (msys2_idx < 0 or git_idx < msys2_idx):
        warnings.warn(
            f"termapy tests: prepending {msys2_bin!r} to PATH for the test "
            f"session.  Git's mingw64\\bin appears in PATH at position "
            f"{git_idx} -- ahead of msys2's at position "
            f"{'absent' if msys2_idx < 0 else msys2_idx}.  Without this fix, "
            f"pytest's gcc subprocess loads Git's libstdc++-6 DLL and "
            f"cc1.exe crashes with NT status 0xC0000139.  Make permanent "
            f"by adding 'export PATH=\"/c/msys64/mingw64/bin:$PATH\"' to "
            f"your .bashrc / .zshrc.",
            RuntimeWarning,
            stacklevel=2,
        )
    # Prepend (drop any existing later occurrence so PATH doesn't grow
    # every test session if conftest reloads).
    parts = [part for part, n in zip(parts, norm) if n != norm_msys2]
    os.environ["PATH"] = os.pathsep.join([msys2_bin] + parts)


_fix_msys2_path_on_windows()
