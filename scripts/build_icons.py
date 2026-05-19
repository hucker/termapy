"""Rasterize termapy.svg to .ico, .icns, and .png.

Run with:
    uvx --with cairosvg --with Pillow --with icnsutil \\
        python scripts/build_icons.py

Inputs:
    src/termapy/templates/icons/termapy.svg

Outputs (committed to the repo alongside the source SVG):
    src/termapy/templates/icons/termapy.ico   (16, 32, 48, 256 Windows)
    src/termapy/templates/icons/termapy.icns  (16..1024 macOS multi-res)
    src/termapy/templates/icons/termapy.png   (256x256 Linux)

Dependencies are ephemeral via ``uvx --with``; they never enter
pyproject.toml or uv.lock.  Re-run only when the SVG changes; the
binaries are the source of truth for the launcher wiring.
"""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
import icnsutil
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICONS_DIR = ROOT / "src" / "termapy" / "templates" / "icons"
SVG = ICONS_DIR / "termapy.svg"

ICO_SIZES = (16, 32, 48, 256)
ICNS_SIZES = (16, 32, 128, 256, 512, 1024)
PNG_SIZE = 256


def render(size: int) -> bytes:
    """Rasterize the SVG at the given square pixel size; return PNG bytes."""
    return cairosvg.svg2png(
        bytestring=SVG.read_bytes(),
        output_width=size,
        output_height=size,
    )


def write_ico() -> Path:
    out = ICONS_DIR / "termapy.ico"
    base = Image.open(io.BytesIO(render(max(ICO_SIZES)))).convert("RGBA")
    # bitmap_format="png" forces PNG-compressed ICO entries at
    # every size; the default (BMP) uses a 1-bit AND mask that
    # flattens partial alpha and can leave a hard halo on
    # Windows Explorer thumbnails.
    base.save(
        out, format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        bitmap_format="png",
    )
    return out


def write_icns() -> Path:
    out = ICONS_DIR / "termapy.icns"
    bundle = icnsutil.IcnsFile()
    # icnsutil derives the icon-type key from the filename suffix
    # (e.g. 256.png -> ic08).  Pass data + a synthetic name; no
    # tempfile required.
    for size in ICNS_SIZES:
        bundle.add_media(file=f"{size}.png", data=render(size))
    bundle.write(out)
    return out


def write_png() -> Path:
    out = ICONS_DIR / "termapy.png"
    Image.open(io.BytesIO(render(PNG_SIZE))).convert("RGBA").save(
        out, format="PNG",
    )
    return out


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"Source SVG not found: {SVG}")
    paths = [write_ico(), write_icns(), write_png()]
    print("Wrote:")
    for p in paths:
        print(f"  {p}  ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
