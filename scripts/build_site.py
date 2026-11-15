#!/usr/bin/env python3
"""Build a clean, directly previewable static site (never writes source index.json)."""
import argparse
from pathlib import Path

from build_content import build as build_content
from build_index import build as build_index
from content import KINDS
from prepare_site import ROOT, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("/tmp/greenlog-site"),
                        help="Output directory (default: /tmp/greenlog-site)")
    args = parser.parse_args()
    prepare(ROOT, args.site_dir)
    for kind in KINDS:
        build_content(ROOT, args.site_dir, kind)
    build_index(ROOT, args.site_dir)
    print(f"Preview: python3 -m http.server --directory {args.site_dir} 8000")


if __name__ == "__main__":
    main()
