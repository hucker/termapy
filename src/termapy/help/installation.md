# Installation

Two commands for each package manager: one to install, one to upgrade.
We strongly recommend [uv](https://docs.astral.sh/uv/); pip works but
installs into whichever Python interpreter you run it with.

⚠️ **Exit any running termapy before upgrading.**  The running program
holds its own binary open, so the package manager can't replace the
files cleanly.  On Windows the upgrade will fail outright with a
file-in-use error.  On macOS and Linux the upgrade command will
usually succeed, but you'll keep running the old code in the live
process until you exit and relaunch -- confusing if you're expecting
a fix or feature from the new release.  Close every termapy window,
run the upgrade command, then relaunch.

## uv (recommended)

Install:

```sh
uv tool install termapy
```

Upgrade:

```sh
uv tool upgrade termapy
```

## pip

Install:

```sh
pip install termapy
```

Upgrade:

```sh
pip install --upgrade termapy
```

## Uninstall

```sh
uv tool uninstall termapy
```

Or, if installed with pip:

```sh
pip uninstall termapy
```
