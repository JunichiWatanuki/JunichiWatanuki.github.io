#!/bin/sh
set -eu
cd "${GITHUB_WORKSPACE:-.}"
exec python3 scripts/build_content.py --kind sample --site-dir "$1"

