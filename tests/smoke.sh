#!/usr/bin/env sh
# Takes three synthetic clips from video to result through the recommended
# flow, tools/find_reuse.py, twice. The first run has to fingerprint every
# clip and find the copy; the second has to give the same answer without
# running ffmpeg or ffprobe at all, because the store recognised what it wrote.
#
# Everything else in tests/ compares signatures that were generated elsewhere,
# on purpose, so that an ffmpeg upgrade cannot fail the comparison's tests.
# This is the one place the real ffmpeg, the real store and the real binary
# run together, which is what catches the signature format drifting, a filter
# argument ffmpeg stops accepting, or the cache no longer recognising its own
# files. It says nothing about accuracy beyond one easy case; benchmark.md is
# for that.
#
#   make smoke                                   # builds the binary, then this
#   MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/smoke.sh
#
# Needs ffmpeg and ffprobe on PATH, and uv or python3 for the script. Skips
# itself, saying so, when ffmpeg is missing; REQUIRE_FFMPEG=1 makes that a
# failure, for CI, where a runner without ffmpeg should not pass by testing
# less.

set -eu

here="$(cd "$(dirname "$0")" && pwd)"
bin="${MPEG7DUPES:-$here/../bin/mpeg7Dupes.elf}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[ -x "$bin" ] || command -v "$bin" >/dev/null 2>&1 || {
    echo "no binary at $bin; build it first or set MPEG7DUPES" >&2
    exit 2
}
case "$bin" in */*) bin="$(cd "$(dirname "$bin")" && pwd)/$(basename "$bin")" ;; esac

for tool in ffmpeg ffprobe; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        if [ "${REQUIRE_FFMPEG:-0}" = 1 ]; then
            echo "smoke.sh: no $tool on PATH, and REQUIRE_FFMPEG=1 says that is a failure" >&2
            exit 1
        fi
        echo "smoke.sh: no $tool on PATH, skipping the smoke test"
        exit 0
    fi
done
real_ffmpeg="$(command -v ffmpeg)"
real_ffprobe="$(command -v ffprobe)"

if command -v uv >/dev/null 2>&1; then
    runner="uv run"
elif [ -x "$HOME/.local/bin/uv" ]; then
    runner="$HOME/.local/bin/uv run"
elif command -v python3 >/dev/null 2>&1; then
    runner="python3"
else
    echo "smoke.sh: no uv and no python3, cannot run tools/find_reuse.py" >&2
    exit 1
fi

failures=0
checks=0

check() {
    checks=$((checks + 1))
    if [ "$2" = "$3" ]; then
        printf '  ok    %s\n' "$1"
    else
        printf '  FAIL  %s\n    expected %s, got %s\n' "$1" "$3" "$2"
        failures=$((failures + 1))
    fi
}

echo "Smoke testing $bin with $real_ffmpeg"
cd "$work"

# a    40 seconds of one pattern, the source
# b    a with five seconds cut from the head and scaled down, the copy that
#      has to be found, starting at 0 in b and at 5 in a
# c    another pattern entirely, which must come back checked and unmatched
enc="-c:v libx264 -preset ultrafast -pix_fmt yuv420p"
mkdir candidates
ffmpeg -v error -f lavfi -i 'mandelbrot=size=320x180:rate=25' -t 40 $enc a.mp4
ffmpeg -v error -ss 5 -i a.mp4 -vf scale=240:136 $enc candidates/b.mp4
ffmpeg -v error -f lavfi -i 'life=size=320x180:rate=25:mold=10' -t 30 $enc candidates/c.mp4

# Stand-ins that log each call and hand it on, so the second run can be shown
# to have read no video: not one call, rather than a call that did little.
mkdir shim
cat > shim/ffmpeg <<SHIM
#!/bin/sh
echo "\$*" >> "$work/ffmpeg.calls"
exec "$real_ffmpeg" "\$@"
SHIM
cat > shim/ffprobe <<SHIM
#!/bin/sh
echo "\$*" >> "$work/ffprobe.calls"
exec "$real_ffprobe" "\$@"
SHIM
chmod +x shim/ffmpeg shim/ffprobe
: > ffmpeg.calls
: > ffprobe.calls

# Reads the facts the checks need out of the record, one per line, so the
# shell never has to parse JSON.
cat > facts.py <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
s = r["summary"]
print("complete", str(s["complete"]).lower())
print("matches", s["matches"])
print("candidates", s["candidates_requested"], s["candidates_matched"],
      s["candidates_checked"], s["candidates_failed"])
for c in r["candidates"]:
    print("candidate", c["name"], c["status"])
for m in r["matches"]:
    print("match", m["source"], m["candidate"], m["coverage_percent"],
          m["framerateratio"], m["start_seconds"], m["source_begin_seconds"],
          m["whole"], str(m["overrun"]).lower())
PY

run_reuse() {
    # shellcheck disable=SC2086
    if $runner "$here/../tools/find_reuse.py" --source a.mp4 \
            --candidates candidates --sig-dir store --json "$1" \
            --ffmpeg "$work/shim/ffmpeg" --ffprobe "$work/shim/ffprobe" \
            --mpeg7dupes "$bin" > "$1.out" 2> "$1.err"; then
        echo 0
    else
        echo $?
    fi
}
fact() { awk -v k="$2" '$1 == k' "$1"; }
calls() { grep -c . "$1" || true; }

# ---- the first run: fingerprint everything, find the copy ----
st=$(run_reuse run1.json)
check "the first run completes" "$st" 0
if [ "$st" != 0 ]; then sed 's/^/    /' run1.json.err; fi
$runner facts.py run1.json > run1.facts 2>/dev/null || : > run1.facts
check "and says it is complete" "$(fact run1.facts complete)" "complete true"
check "and fingerprinted all three clips" \
    "$(grep -c 'signature=filename=' ffmpeg.calls || true)" 3
check "and reports one match" "$(fact run1.facts matches)" "matches 1"
check "b is matched and c is checked" \
    "$(fact run1.facts candidate | sort | tr '\n' ';')" \
    "candidate b.mp4 matched;candidate c.mp4 checked;"
match="$(fact run1.facts match)"
set -- $match
# $2 source $3 candidate $4 coverage $5 ratio $6 start $7 source_begin $8 whole $9 overrun
check "the match is b holding a" "$2 $3" "a.mp4 b.mp4"
check "at the same speed" "$5" "1.0"
check "covering most of a" \
    "$(awk -v c="$4" 'BEGIN { print (c >= 80 && c <= 100) ? "80..100" : c }')" "80..100"
check "and not more than there is" "$9" "false"
# b starts at 0 of itself and five seconds into a. The walk can accept a frame
# or two of the other material past a cut, so this allows a second either way.
check "starting at the head of b" \
    "$(awk -v t="$6" 'BEGIN { print (t <= 1.0) ? "0..1" : t }')" "0..1"
check "five seconds into a" \
    "$(awk -v t="$7" 'BEGIN { print (t >= 4.0 && t <= 6.0) ? "4..6" : t }')" "4..6"
check "the printed line says the same" \
    "$(grep -c 'candidates/b.mp4   used a.mp4' run1.json.out || true)" 1
check "and c is not on it" "$(grep -c 'c.mp4   used' run1.json.out || true)" 0

# ---- the second run: the same answer, and no video read ----
before_ffmpeg=$(calls ffmpeg.calls)
before_ffprobe=$(calls ffprobe.calls)
st=$(run_reuse run2.json)
check "the second run completes" "$st" 0
if [ "$st" != 0 ]; then sed 's/^/    /' run2.json.err; fi
$runner facts.py run2.json > run2.facts 2>/dev/null || : > run2.facts
# The script asks ffmpeg for its version once per run, to record it. That is
# the only call allowed: nothing may be decoded, probed or fingerprinted.
tail -n +"$((before_ffmpeg + 1))" ffmpeg.calls > run2.ffmpeg
check "and reads no video with ffmpeg" "$(grep -c -- ' -i ' run2.ffmpeg || true)" 0
check "its only ffmpeg call being for the version" \
    "$(grep -v -- '^-version$' run2.ffmpeg | grep -c . || true)" 0
check "and runs ffprobe not once" "$(calls ffprobe.calls)" "$before_ffprobe"
check "and reports the same match with the same numbers" \
    "$(fact run2.facts match)" "$match"
check "and the same statuses" \
    "$(fact run2.facts candidate | sort | tr '\n' ';')" \
    "$(fact run1.facts candidate | sort | tr '\n' ';')"

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi

# Input format and geometry contracts also require the real decoder.
MPEG7DUPES="$bin" $runner "$here/video_io.py"
MPEG7DUPES="$bin" $runner "$here/black_video.py"
MPEG7DUPES="$bin" $runner "$here/crop_video.py"

MPEG7DUPES="$bin" $runner "$here/profile_video.py"
