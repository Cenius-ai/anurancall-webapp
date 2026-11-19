#!/usr/bin/env bash
# AnuranCall guided demo — fully non-interactive end to end.
#
# It records sessions (tap mode and pasted-interval mode), then reads them back
# through every view. Everything goes to demo/sessions.json so your own survey
# log in ~/.anurancall/sessions.json is never touched.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
DATA="demo/sessions.json"

mkdir -p demo
rm -f "$DATA"

day_offset() {
  "$PYTHON" -c 'import datetime, sys; print((datetime.date.today() + datetime.timedelta(days=int(sys.argv[1]))).isoformat())' "$1"
}

quote_arg() {
  case "$1" in
    *[[:space:]]*) printf "'%s'" "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

show_command() {
  printf '\n$ python3 anurancall.py --data %s' "$DATA"
  for argument in "$@"; do
    printf ' %s' "$(quote_arg "$argument")"
  done
  printf '\n'
}

run() {
  show_command "$@"
  "$PYTHON" anurancall.py --data "$DATA" "$@"
}

LAST_ID=""
record() {
  show_command "$@"
  local output
  output="$("$PYTHON" anurancall.py --data "$DATA" "$@")"
  printf '%s\n' "$output"
  LAST_ID="$(printf '%s\n' "$output" | awk '/^saved session /{print $3}')"
}

echo "AnuranCall demo ($("$PYTHON" anurancall.py --version))"
echo "writes to $DATA — your own ~/.anurancall/sessions.json is untouched"

echo
echo "=== 1. an empty log, before any survey"
run sessions

echo
echo "=== 2. tap mode: press enter once per call (three calls, about half a second apart)"
echo "\$ { sleep .55; echo; sleep .5; echo; sleep .6; echo; echo q; } | \\"
echo "    python3 anurancall.py --data $DATA count --species 'Spring Peeper' \\"
echo "      --site 'Mud Pond' --temp 16.0 --notes 'chorus from the north bank, water still'"
TAP_OUTPUT="$(
  { sleep 0.55; echo; sleep 0.5; echo; sleep 0.6; echo; echo q; } |
    "$PYTHON" anurancall.py --data "$DATA" count \
      --species 'Spring Peeper' --site 'Mud Pond' --temp 16.0 \
      --notes 'chorus from the north bank, water still'
)"
printf '%s\n' "$TAP_OUTPUT"
TAP_ID="$(printf '%s\n' "$TAP_OUTPUT" | awk '/^saved session /{print $3}')"

echo
echo "=== 3. pasted intervals from a field sheet (backdated with --date)"
record count --species 'Wood Frog' --site 'Beaver Marsh' --temp 12.0 \
  --notes 'two males clucking, ice out last week' --intervals '2.0, 2.4, 1.8' \
  --date "$(day_offset -12)"
record count --species 'Spring Peeper' --site 'Mud Pond' --temp 14.5 \
  --notes 'sparse, cold front moving in' --intervals '1.4, 1.6, 1.5, 1.3' \
  --date "$(day_offset -5)"
record count --species 'Spring Peeper' --site 'Mud Pond' --temp 19.0 \
  --notes 'full chorus, peepers overlapping the wood frogs' --intervals '0.9, 1.0, 1.1'

echo
echo "=== 4. the whole log, newest first"
run sessions

echo
echo "=== 5. species-by-date phenology"
run phenology

echo
echo "=== 6. per-pond site summary"
run sites

echo
echo "=== 7. one session in full"
run show "$TAP_ID"

echo
echo "=== 8. correcting a temperature after the fact"
run edit "$TAP_ID" --temp 16.5

echo
echo "=== 9. removing a mistaken session"
record count --species 'American Bullfrog' --site 'Quarry Pool' --intervals '3.1, 2.9, 3.4' --date "$(day_offset -1)"
run delete "$LAST_ID" --yes

echo
echo "=== 10. machine-readable output for piping"
printf '\n$ python3 anurancall.py --data %s sessions --json | python3 -c ...\n' "$DATA"
"$PYTHON" anurancall.py --data "$DATA" sessions --json |
  "$PYTHON" -c 'import json, sys
doc = json.load(sys.stdin)
newest = doc["sessions"][0]
print("json parsed: %d of %d sessions; newest id %s" % (doc["count"], doc["total_sessions"], newest["id"]))
print("session fields: " + ", ".join(sorted(newest)))'

echo
echo "demo complete — the store is $DATA"
echo "  read it yourself : python3 anurancall.py --data $DATA sessions"
echo "  tidy up          : rm $DATA"
