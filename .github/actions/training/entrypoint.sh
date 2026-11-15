#!/bin/sh
set -eu
cd "${GITHUB_WORKSPACE:-.}"
# build_content.py decodes each FIT with garmin-fit-sdk and writes JSON and HTML.
exec python3 scripts/build_content.py --kind training --site-dir "$1"

