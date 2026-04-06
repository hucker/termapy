# Register vendored pyserial before any test imports serial.
# This mirrors what termapy/__init__.py does at app startup.
import termapy.vendor  # noqa: F401
