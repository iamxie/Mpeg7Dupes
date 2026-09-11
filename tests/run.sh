#!/usr/bin/env sh
# Compares the checked-in fixtures and checks the result two ways: against a
# recorded copy of the whole output, and against named properties that say why
# each number is what it is.
#
# The recorded copy catches any change at all. The named checks say what broke
# when one does, which a diff of ten rows does not.
#
#   sh tests/run.sh                                  # uses bin/mpeg7Dupes.elf
#   MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/run.sh
#   UPDATE=1 sh tests/run.sh                         # rewrite the recorded copy
#
# Row order is not stable, because the comparison runs on every core and rows
# are written as they finish, so everything here sorts first.

set -eu

here="$(cd "$(dirname "$0")" && pwd)"
bin="${MPEG7DUPES:-$here/../bin/mpeg7Dupes.elf}"
expected="$here/expected/compare-longest.csv"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[ -x "$bin" ] || command -v "$bin" >/dev/null 2>&1 || {
    echo "no binary at $bin; build it first or set MPEG7DUPES" >&2
    exit 2
}

# Every option spelled out, so the pass means the same on a build with other
# defaults. -k 1 and -b 0.1 so every pair is reported and the checks can look
# at the ones that do not match too. -m longest is the only mode since build
# 10; up to build 9 a second pass ran -m full, whose early stop several checks
# here described, and its recorded copy, compare.csv, went with the mode.
( cd "$here/fixtures" && ls -1 *.bin > "$work/list.txt" )
( cd "$here/fixtures" && "$bin" -f csv -m longest -i 0 -k 1 -b 0.1 \
    -l "$work/list.txt" > "$work/out.csv" 2> "$work/out.err" )

sort "$work/out.csv" > "$work/actual.sorted"

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

# whole is read as the last column rather than by number, because it is
# documented to stay last and columns get added before it.
whole() {
    awk -F, -v a="$1" -v b="$2" \
        '$1 == a && $2 == b { print $NF } $1 == b && $2 == a { print $NF }' \
        "$work/out.csv"
}

# field: pair, column number
field() {
    awk -F, -v a="$1" -v b="$2" -v c="$3" \
        '$1 == a && $2 == b { print $c } $1 == b && $2 == a { print $c }' \
        "$work/out.csv"
}

echo "Comparing the fixtures with $bin"

# Six fixtures make fifteen pairs. The five with unrelated in them produce no
# row at all: the coarse filter rejects every pair of segments that shares
# nothing, so no candidate is ever walked, and printCSV drops a row whose
# score is 0 either way. Worth pinning: a pair missing from the output does
# not mean it was never compared. Up to build 6 the filter passed everything
# and four of those five came back as rows of five to nine noise frames.
# Matched loosely on purpose: the build number changes with almost every
# commit, and a test that has to be edited each time is a test that gets
# edited without being read. What matters is that the banner is there at the
# default verbosity, since a run whose build is unknown has to be repeated.
banner='mpeg7dupes v[0-9]+\.[0-9]+ b[0-9]+'
check "every run says which build produced it" \
    "$(grep -cE "$banner" "$work/out.err")" 1

check "ten of the fifteen pairs are reported" \
    "$(tail -n +2 "$work/out.csv" | grep -c .)" 10

# A re-encode at half the width is still the same clip from end to end.
check "base vs scaled covers the whole clip" \
    "$(field base.bin scaled.bin 4),$(whole base.bin scaled.bin)" "150,1"

# The extract is 12 seconds of the original, 60 frames at 5 fps, and it matches
# from its first frame to its last.
check "base vs excerpt finds the extract end to end" \
    "$(field base.bin excerpt.bin 4),$(whole base.bin excerpt.bin)" "60,1"

# headinsert is ten seconds of another pattern followed by the extract, so the
# extract begins exactly ten seconds in and runs to the end. Only the endpoint
# columns can say that: the seed frame sits somewhere in the middle of a match,
# and the offset between the two seeds only locates the start when all of the
# shorter clip was used.
check "the endpoints locate the extract inside headinsert" \
    "$(field excerpt.bin headinsert.bin 14),$(field excerpt.bin headinsert.bin 15)" \
    "10.00,21.80"
check "and say the extract matched from its own first frame to its last" \
    "$(field excerpt.bin headinsert.bin 12),$(field excerpt.bin headinsert.bin 13)" \
    "0.00,11.80"

# These two share content that sits inside both files, so neither side reaches
# both of its ends and the candidate has to be chosen on its merits. That
# choice used to be inverted and settled on a few frames of noise: this scored
# 14 before the fix and in the hundreds after.
check "base vs headinsert scores like a real match, not noise" \
    "$([ "$(field base.bin headinsert.bin 3)" -ge 100 ] && echo yes || echo no)" yes
check "headinsert vs scaled scores like a real match, not noise" \
    "$([ "$(field headinsert.bin scaled.bin 3)" -ge 100 ] && echo yes || echo no)" yes
check "and the match is the sixty frames of the extract" \
    "$(field base.bin headinsert.bin 4),$(field base.bin headinsert.bin 12),$(field base.bin headinsert.bin 13)" \
    "63,9.00,20.80"

# tailinsert is the extract followed by ten seconds of another pattern, so the
# extract sits at 9 to 21 seconds of base. A search that stopped at the first
# candidate reaching both ends, which full did up to build 9, settled here for
# a match somewhere else in the clip, worth a score of 8 and starting at 18.40.
# The search carries on and finds the real one; that is why the early stop
# went.
check "the match is the region that is really shared" \
    "$(field base.bin tailinsert.bin 12),$(field base.bin tailinsert.bin 13)" \
    "9.00,20.80"

# Nothing shares content with unrelated, so every pair involving it has to stay
# down in the noise.
check "unrelated never scores above the noise floor" \
    "$(awk -F, '$1 ~ /unrelated/ || $2 ~ /unrelated/ { if ($3 > 20) n++ } END { print n+0 }' \
        "$work/out.csv")" 0

# One recorded copy, of the one mode. Up to build 9 a second copy, compare.csv,
# pinned full's early stop as it was, and went with the mode.
if [ -f "$expected" ] && [ "${UPDATE:-0}" != "1" ]; then
    if diff -u "$expected" "$work/actual.sorted" > "$work/diff" 2>&1; then
        printf '  ok    output matches tests/expected/compare-longest.csv\n'
        checks=$((checks + 1))
    else
        printf '  FAIL  output differs from tests/expected/compare-longest.csv\n'
        sed 's/^/    /' "$work/diff"
        checks=$((checks + 1))
        failures=$((failures + 1))
    fi
else
    cp "$work/actual.sorted" "$expected"
    printf '  ..    recorded tests/expected/compare-longest.csv\n'
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi
