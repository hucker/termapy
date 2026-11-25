# Vendored Dependencies

## xmodem (xmodem.py)

- Source: <https://pypi.org/project/xmodem/>
- Version: 0.4.7
- License: MIT
- Author: Wijnand Modderman, Jeff Quast, Andrew Leech

## ymodem (ymodem/)

- Source: <https://pypi.org/project/ymodem/>
- Version: 1.5.3
- License: MIT
- Author: alexwoo (<alexwoo@foxmail.com>)
- Modification: replaced `ordered-set` dependency with built-in `set()`
- Modification: the receiver reduces the sender's file name to its base name before opening it (`Socket.py`, marked `termapy:`)

## pyserial (serial/)

- Source: <https://github.com/pyserial/pyserial>, branch `master` at commit
  `a5c48d445fbc1943d4fabf8d9090a50fda3172fd` (2026-02-27)
- Version: 3.5 and the unreleased changes after it on `master`; `__version__` still reads `3.5`
- License: BSD-3-Clause
- Author: Chris Liechti
- Last upstream release: 3.5, November 2020 (maintained again since late 2025, no release since)
- The `serial/` package as that commit has it, which no longer ships
  `serialcli.py` (IronPython) or `serialjava.py` (Jython) (registered into sys.modules via vendor/__init__.py)
- Modification: `serialposix.py` reads the machine from `os.uname().machine` instead of
  `platform.machine()` (the same value on POSIX), so no start loads `platform`
  (marked `termapy:`)
