"""Refresh termapy's bundled USB vendor table from upstream usb.ids.

Reads the canonical USB ID Repository file from linux-usb.org (or a
local copy) and emits ``src/termapy/_usb_vendor_full.py`` -- a Python
module containing a single ``USB_VENDORS_FULL: dict[int, str]``
mapping every assigned USB Vendor ID to its canonical vendor name.

Why a generated Python module rather than the raw text file?

  - Runtime cost: zero parse at lookup time -- the dict is loaded by
    the normal Python import path, same as termapy's hand-curated
    ``USB_VENDORS``.
  - Termapy keeps a strict import-discipline (``cli_flags`` must not
    do I/O at import); a generated dict respects that.
  - Existing ``vendor_for(vid)`` API and call sites stay unchanged.

The generated file is committed to git.  Run this script before each
release (or whenever upstream gets a notable update) to refresh.
Diffs will typically show only newly-assigned VIDs.

Usage:

    # Fetch latest from linux-usb.org and regenerate.
    python scripts/refresh_usb_ids.py

    # Use a specific local copy (useful in air-gapped or pinned setups).
    python scripts/refresh_usb_ids.py --source path/to/usb.ids

    # Specify a custom URL (e.g. a frozen mirror or fork).
    python scripts/refresh_usb_ids.py --source https://example.com/usb.ids

The data file is plain text under a permissive redistribution
license; see https://www.linux-usb.org/usb.ids for the canonical
copy.

Stdlib only -- no requests, no third-party deps.  Safe to run in CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import urllib.request
from pathlib import Path

# Default upstream URL.  linux-usb.org publishes via plain HTTP; the
# GitHub mirror is HTTPS and tends to be more reliable.
DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/usbids/usbids/master/usb.ids"
)
FALLBACK_SOURCE = "http://www.linux-usb.org/usb.ids"

# Repo paths.
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "src" / "termapy" / "_usb_vendor_full.py"


def fetch(source: str) -> tuple[str, str | None]:
    """Fetch the usb.ids text from a URL or local path.

    Returns ``(text, last_modified)``.  ``last_modified`` is the
    upstream HTTP ``Last-Modified`` header when the source is a URL,
    or None for local-file sources.  Stored in the generated module
    so we can later detect whether upstream has changed without
    re-downloading the body.
    """
    last_modified: str | None = None
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as resp:
            data = resp.read()
            last_modified = resp.headers.get("Last-Modified")
    else:
        data = Path(source).read_bytes()
    # usb.ids is officially UTF-8.  Some older copies have stray non-UTF-8
    # vendor names (umlauts in trade names); fall back to latin-1 which
    # never raises.
    try:
        return data.decode("utf-8"), last_modified
    except UnicodeDecodeError:
        return data.decode("latin-1"), last_modified


def check_upstream(source: str) -> str | None:
    """HEAD upstream and return its current ``Last-Modified``, or None.

    Used by ``--check`` mode and ``/term.usb_db`` to detect whether
    upstream has changed since our last refresh.  Returns None on any
    error (offline, server doesn't supply the header) so callers can
    treat it as "unknown" rather than crashing.
    """
    if not source.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(source, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.headers.get("Last-Modified")
    except Exception:
        return None


# Vendor lines start at column 0 with a 4-hex VID, two spaces, then the
# vendor name.  Product lines start with a tab and are skipped.  Class
# / subclass / language sections start with a non-tab non-hex prefix
# (``C ``, ``AT ``, ``HID ``, etc.) and are also skipped.
_VENDOR_LINE = re.compile(r"^([0-9a-fA-F]{4})\s+(.+)$")


def parse_vendors(text: str) -> dict[int, str]:
    """Extract VID -> vendor-name mapping from usb.ids text."""
    vendors: dict[int, str] = {}
    for line in text.splitlines():
        # Skip comments, blank lines, product (tab-indented) lines, and
        # the class / language sections that start with a letter prefix.
        if not line or line.startswith("#") or line.startswith("\t"):
            continue
        m = _VENDOR_LINE.match(line)
        if not m:
            continue
        vid = int(m.group(1), 16)
        name = m.group(2).strip()
        vendors[vid] = name
    return vendors


def emit_python_module(
    vendors: dict[int, str],
    source_url: str,
    upstream_last_modified: str | None,
) -> str:
    """Build the contents of the generated Python module.

    Emits the dict plus three metadata constants:

      - ``GENERATED_DATE``: when we last ran the refresh (local clock).
      - ``UPSTREAM_LAST_MODIFIED``: upstream HTTP ``Last-Modified`` at
        fetch time.  None when the source is a local file.  Used by
        /term.usb_db and the ``--check`` mode to detect whether
        upstream has been updated since our last refresh.
      - ``SOURCE_URL``: where the data came from.
    """
    today = dt.date.today().isoformat()
    sorted_items = sorted(vendors.items())
    body_lines = []
    for vid, name in sorted_items:
        # repr() handles quoting + escapes for any character including
        # non-ASCII; safer than manual escaping.
        body_lines.append(f"    0x{vid:04X}: {name!r},")
    body = "\n".join(body_lines)
    return (
        '"""USB Vendor ID -> canonical vendor name (generated).\n'
        "\n"
        "DO NOT EDIT BY HAND.  Generated by ``scripts/refresh_usb_ids.py``\n"
        "from the upstream USB ID Repository.  To refresh, run::\n"
        "\n"
        "    python scripts/refresh_usb_ids.py\n"
        "\n"
        f"Source:    {source_url}\n"
        f"Generated: {today}\n"
        f"Upstream:  {upstream_last_modified or 'unknown'}\n"
        f"Entries:   {len(vendors)}\n"
        "\n"
        "Used as a fallback by ``termapy.usb_vendor.vendor_for()`` when a\n"
        "VID isn't present in the curated ``USB_VENDORS`` short-form\n"
        "table.  Names here are the canonical USB-IF assignments and may\n"
        "be long; ``usb_mfg.mfg()`` handles narrow-column display.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "# Metadata for /term.usb_db and other introspection callers.\n"
        f"GENERATED_DATE: str = {today!r}\n"
        f"UPSTREAM_LAST_MODIFIED: str | None = {upstream_last_modified!r}\n"
        f"SOURCE_URL: str = {source_url!r}\n"
        "\n"
        "USB_VENDORS_FULL: dict[int, str] = {\n"
        f"{body}\n"
        "}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"URL or local path to usb.ids (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help=f"Path to write the Python module (default: {OUTPUT_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Don't refresh; HEAD upstream and compare its Last-Modified "
            "header against the value baked into the generated module.  "
            "Exits 0 if local matches upstream, 1 if upstream is newer "
            "(or unknown)."
        ),
    )
    args = parser.parse_args()

    if args.check:
        return _run_check(args.source, Path(args.output))

    print(f"==> Fetching {args.source}")
    try:
        text, last_modified = fetch(args.source)
    except Exception as e:
        if args.source == DEFAULT_SOURCE:
            print(f"  primary failed ({e}); trying {FALLBACK_SOURCE}")
            text, last_modified = fetch(FALLBACK_SOURCE)
            args.source = FALLBACK_SOURCE
        else:
            raise

    print(f"==> Parsing {len(text):,} bytes...")
    vendors = parse_vendors(text)
    print(f"==> Found {len(vendors):,} vendor entries")

    if len(vendors) < 1000:
        print(
            f"  warning: parsed only {len(vendors)} vendors -- did the "
            "format change?",
            file=sys.stderr,
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        emit_python_module(vendors, args.source, last_modified),
        encoding="utf-8",
    )
    print(f"==> Wrote {out_path}")
    print(f"OK  {len(vendors):,} vendors written")
    if last_modified:
        print(f"OK  upstream Last-Modified: {last_modified}")
    return 0


def _run_check(source: str, out_path: Path) -> int:
    """Compare upstream Last-Modified against the locally-stored value."""
    if not out_path.exists():
        print(
            f"FAIL no local generated file at {out_path}; run without --check first",
            file=sys.stderr,
        )
        return 1
    text = out_path.read_text(encoding="utf-8")
    m = re.search(
        r"^UPSTREAM_LAST_MODIFIED:\s*[^=]+=\s*(\S.*)$",
        text,
        flags=re.MULTILINE,
    )
    local_lm: str | None = None
    if m:
        try:
            local_lm = ast_literal_eval(m.group(1))
        except Exception:
            pass

    upstream_lm = check_upstream(source)
    print(f"Local stored:       {local_lm!r}")
    print(f"Upstream now:       {upstream_lm!r}")
    if upstream_lm is None:
        print("UNKNOWN: HEAD failed; can't determine upstream state")
        return 1
    if local_lm is None:
        print("UNKNOWN: local file lacks UPSTREAM_LAST_MODIFIED; refresh recommended")
        return 1
    if local_lm == upstream_lm:
        print("OK  up to date")
        return 0
    print("STALE: upstream has a newer Last-Modified; refresh recommended")
    return 1


def ast_literal_eval(s: str):
    import ast
    return ast.literal_eval(s)


if __name__ == "__main__":
    sys.exit(main())
