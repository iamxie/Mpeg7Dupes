#!/usr/bin/env sh
# Compares the checked-in fixtures and checks the result two ways: against a
# recorded copy of the whole output, and against named properties that say why
# each number is what it is.
#
# The recorded copy catches any change at all. The named checks say what broke
# when one does, which a diff of fourteen rows does not.
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
expected="$here/expected/compare.csv"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[ -x "$bin" ] || command -v "$bin" >/dev/null 2>&1 || {
    echo "no binary at $bin; build it first or set MPEG7DUPES" >&2
    exit 2
}

# -i 0 because any other value truncates the match, -k 1 and -b 0.1 so every
# pair is reported and the checks can look at the ones that do not match too.
( cd "$here/fixtures" && ls -1 *.bin > "$work/list.txt" )
( cd "$here/fixtures" && "$bin" -f csv -m full -i 0 -k 1 -b 0.1 \
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

# Six fixtures make fifteen pairs, but printCSV drops a row whose score is 0,
# so excerpt against unrelated never appears. Worth pinning: a pair missing
# from the output does not mean it was never compared.
check "fourteen of the fifteen pairs are reported" \
    "$(tail -n +2 "$work/out.csv" | grep -c .)" 14

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
# 14 before the fix and 506 after. Guarding the score rather than the frame
# count because the gap is far wider there.
check "base vs headinsert scores like a real match, not noise" \
    "$([ "$(field base.bin headinsert.bin 3)" -ge 100 ] && echo yes || echo no)" yes
check "headinsert vs scaled scores like a real match, not noise" \
    "$([ "$(field headinsert.bin scaled.bin 3)" -ge 100 ] && echo yes || echo no)" yes

# Nothing shares content with unrelated, so every pair involving it has to stay
# down in the noise.
check "unrelated never scores above the noise floor" \
    "$(awk -F, '$1 ~ /unrelated/ || $2 ~ /unrelated/ { if ($3 > 20) n++ } END { print n+0 }' \
        "$work/out.csv")" 0

if [ -f "$expected" ] && [ "${UPDATE:-0}" != "1" ]; then
    if diff -u "$expected" "$work/actual.sorted" > "$work/diff" 2>&1; then
        printf '  ok    output matches tests/expected/compare.csv\n'
        checks=$((checks + 1))
    else
        printf '  FAIL  output differs from tests/expected/compare.csv\n'
        sed 's/^/    /' "$work/diff"
        checks=$((checks + 1))
        failures=$((failures + 1))
    fi
else
    cp "$work/actual.sorted" "$expected"
    printf '  ..    recorded tests/expected/compare.csv\n'
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi
