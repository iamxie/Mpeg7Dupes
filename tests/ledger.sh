#!/usr/bin/env sh
# Checks that -s makes an interrupted run resumable and a finished one cheap.
#
# The ledger's failure mode is silent: a pair that is wrongly skipped never
# appears in the output and nothing says so. Every check here therefore counts
# rows against a known total rather than looking for an error.
#
#   sh tests/ledger.sh
#   MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/ledger.sh

set -eu

here="$(cd "$(dirname "$0")" && pwd)"
bin="${MPEG7DUPES:-$here/../bin/mpeg7Dupes.elf}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

[ -x "$bin" ] || command -v "$bin" >/dev/null 2>&1 || {
    echo "no binary at $bin; build it first or set MPEG7DUPES" >&2
    exit 2
}

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

cd "$here/fixtures"
ls -1 *.bin > "$work/list.txt"

run() {
    # $1 output file, $2 ledger or empty
    if [ -n "$2" ]; then
        "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/list.txt" -s "$2" \
            > "$1" 2>> "$work/log"
    else
        "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/list.txt" \
            > "$1" 2>> "$work/log"
    fi
}

rows() { tail -n +2 "$1" | grep -c . || true; }

echo "Checking the ledger with $bin"

# Six fixtures make fifteen pairs. Only fourteen reach the output, because a
# pair that scores 0 is dropped, so the ledger has to carry the fifteenth: that
# is the whole reason it records attempts rather than results.
run "$work/full.csv" "$work/a.ledger"
check "a fresh run records every pair it attempted" \
    "$(wc -l < "$work/a.ledger" | tr -d ' ')" 15
check "and reports the fourteen that scored" "$(rows "$work/full.csv")" 14

# Resuming: seed a ledger with five pairs that did reach the output, and only
# the rest should be compared again.
tail -n +2 "$work/full.csv" | head -5 | cut -d, -f1,2 | tr ',' '\t' \
    > "$work/b.ledger"
run "$work/b.csv" "$work/b.ledger"
check "pairs already in the ledger are not compared again" \
    "$(rows "$work/b.csv")" 9
check "and the ledger ends up complete either way" \
    "$(sort -u "$work/b.ledger" | grep -c .)" 15

# The two halves have to add up to the whole, with nothing repeated and nothing
# lost. This is the property the old session file broke.
tail -n +2 "$work/full.csv" | head -5 > "$work/joined.csv"
tail -n +2 "$work/b.csv" >> "$work/joined.csv"
sort "$work/joined.csv" > "$work/joined.sorted"
tail -n +2 "$work/full.csv" | sort > "$work/full.sorted"
check "the halves rejoin into exactly one uninterrupted run" \
    "$(diff "$work/joined.sorted" "$work/full.sorted" > /dev/null \
        && echo same || echo differs)" same

# A finished batch costs nothing to run again.
run "$work/again.csv" "$work/a.ledger"
check "rerunning a finished batch compares nothing" \
    "$(rows "$work/again.csv")" 0

# Order must not matter: the same pair written the other way round still counts
# as done.
awk -F'\t' '{print $2 "\t" $1}' "$work/b.ledger" | head -15 > "$work/c.ledger"
run "$work/c.csv" "$work/c.ledger"
check "a pair recorded in the other order still counts as done" \
    "$(rows "$work/c.csv")" 0

# A run killed mid-write leaves a partial last line. That costs one recomputed
# pair, not a refusal to start.
head -14 "$work/a.ledger" > "$work/d.ledger"
printf 'tests/fixtures/base' >> "$work/d.ledger"
run "$work/d.csv" "$work/d.ledger"
check "a truncated last line costs one pair, not the run" \
    "$(rows "$work/d.csv")" 1

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi
