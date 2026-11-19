#!/usr/bin/env bash
# AnuranCall install: project-level Python dependencies only, then EXIT.
#
# It never installs system packages, never needs sudo, never prompts and never
# starts the tool in the foreground. System prerequisites (Python 3.9+) are
# listed in INSTALL.md and are the only thing this script does not handle.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: $PYTHON not found on PATH — install Python 3.9+ (see INSTALL.md)" >&2
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "error: AnuranCall needs Python 3.9 or newer, found $("$PYTHON" -V 2>&1)" >&2
  exit 1
fi

echo "==> installing dev dependencies (the tool itself is standard-library only)"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
"$PYTHON" -m pip install -r requirements.txt

echo "==> self-check: importing the tool"
"$PYTHON" -c 'import anurancall; print("anurancall " + anurancall.VERSION + " imports cleanly")'

echo "==> self-check: CLI wiring"
"$PYTHON" anurancall.py --help >/dev/null

echo "==> self-check: dev test suite collects"
"$PYTHON" -m unittest discover tests -q >/dev/null

cat <<'SUMMARY'

setup complete — no server to start, this script exits here.
  record a session   : python3 anurancall.py count --species 'Spring Peeper' --site 'Mud Pond'
  read the log       : python3 anurancall.py sessions
  phenology table    : python3 anurancall.py phenology
  guided demo        : bash demo.sh
  test suite         : python3 -m unittest discover tests
SUMMARY
