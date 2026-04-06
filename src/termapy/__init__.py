import termapy.vendor  # noqa: F401 — register vendored pyserial before anything imports serial

from .app import main as run

__all__ = ["run"]
