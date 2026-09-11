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
# Pair lines only: the lines starting with # are the ledger's record of the
# run and its inputs, not pairs.
pairs() { grep -v '^#' "$1" | grep -c . || true; }

echo "Checking the ledger with $bin"

# Six fixtures make fifteen pairs. Ten reach the output: the five pairs with
# unrelated in them produce no row, since the coarse filter rejects every
# pair of segments that shares nothing, and a pair that scores 0 is dropped
# either way. So the ledger has to carry the other five: that is the whole
# reason it records attempts rather than results.
run "$work/full.csv" "$work/a.ledger"
check "a fresh run records every pair it attempted" "$(pairs "$work/a.ledger")" 15
check "and reports the ten that scored" "$(rows "$work/full.csv")" 10

# Resuming: seed a ledger with five pairs that did reach the output, and only
# the rest should be compared again.
tail -n +2 "$work/full.csv" | head -5 | cut -d, -f1,2 | tr ',' '\t' \
    > "$work/b.ledger"
run "$work/b.csv" "$work/b.ledger"
check "pairs already in the ledger are not compared again" \
    "$(rows "$work/b.csv")" 5
check "and the ledger ends up complete either way" \
    "$(grep -v '^#' "$work/b.ledger" | sort -u | grep -c .)" 15

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
grep -v '^#' "$work/b.ledger" | awk -F'\t' '{print $2 "\t" $1}' | head -15 > "$work/c.ledger"
run "$work/c.csv" "$work/c.ledger"
check "a pair recorded in the other order still counts as done" \
    "$(rows "$work/c.csv")" 0

# A run killed mid-write leaves a partial last line. That costs one recomputed
# pair, not a refusal to start. The pair taken out is one that has a row, so
# the recomputation shows in the output; the pairs land in the ledger in
# completion order, so the last line could be any of them.
grep -v "$(printf 'base.bin\tscaled.bin')" "$work/a.ledger" > "$work/d.ledger"
printf 'base.bin' >> "$work/d.ledger"
run "$work/d.csv" "$work/d.ledger"
check "a truncated last line costs one pair, not the run" \
    "$(rows "$work/d.csv"),$(tail -n +2 "$work/d.csv" | cut -d, -f1,2)" \
    "1,base.bin,scaled.bin"

# ---- the run record ----
# A ledger says what it was written for: the build, the binary's digest, the
# settings, and every input's content. A resume that is not the same run is
# refused before a single pair is reused.
check "a fresh ledger carries one run record" \
    "$(grep -c '^#run' "$work/a.ledger")" 1
check "and one identity line per input" \
    "$(grep -c '^#input' "$work/a.ledger")" 6
check "a hand-seeded ledger without a record is reused, with a warning" \
    "$(grep -q 'no run record' "$work/log" && echo warned || echo silent)" warned
check "and is given a record for next time" \
    "$(grep -c '^#run' "$work/b.ledger")" 1

refused() {
    # $1 ledger, then the extra arguments; prints "refused <n rows>"
    local ledger="$1"; shift
    if "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/list.txt" -s "$ledger" \
            "$@" > "$work/r.csv" 2> "$work/r.err"; then
        echo "accepted $(rows "$work/r.csv")"
    else
        echo "refused $(rows "$work/r.csv")"
    fi
}

check "other settings are refused and nothing is compared" \
    "$(refused "$work/a.ledger" -x 250)" "refused 0"
check "and the message names the settings on both sides" \
    "$(grep -c -- '-x 250.*-x 290\|-x 290.*-x 250' "$work/r.err")" 1
# Refusing is half of it. The message has to say that the recorded pairs and
# the output beside them belong to what wrote them, and that the ways on are
# that build or those settings again, or a rerun of the whole list; editing
# the #run line by hand is no longer suggested.
check "and says the pairs were made with other settings, and the two ways on" \
    "$(grep -c 'go back to the settings they were made with.*start over' "$work/r.err")" 1
sed 's/binary=[0-9a-f]*/binary=0000000000000000/' "$work/a.ledger" > "$work/e.ledger"
check "a ledger written by another binary is refused" \
    "$(refused "$work/e.ledger")" "refused 0"
sed 's/version=v[0-9.]* b[0-9]*/version=v0.1 b0/' "$work/a.ledger" > "$work/f.ledger"
check "and so is one written by another build" \
    "$(refused "$work/f.ledger")" "refused 0"
check "and that one says to go back to that build or start over" \
    "$(grep -c 'go back to the build they were made with.*start over' "$work/r.err")" 1

# ---- a new input ----
# Adding a file compares only the pairs it forms; the rest stay skipped.
cp unrelated.bin "$work/seventh.bin"
{ cat "$work/list.txt"; echo "$work/seventh.bin"; } > "$work/seven.txt"
"$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/seven.txt" > "$work/seven_full.csv" 2>> "$work/log"
cp "$work/a.ledger" "$work/g.ledger"
"$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/seven.txt" -s "$work/g.ledger" \
    > "$work/g.csv" 2>> "$work/log"
check "a new input adds exactly its own pairs" \
    "$(rows "$work/g.csv")" "$(( $(rows "$work/seven_full.csv") - $(rows "$work/full.csv") ))"
check "and the ledger then holds all twenty-one" \
    "$(grep -v '^#' "$work/g.ledger" | grep -c .)" 21
check "and seven identity lines" "$(grep -c '^#input' "$work/g.ledger")" 7

# ---- a changed input ----
# A signature regenerated with other settings, or replaced, makes every pair
# it was in stale. Same path, other bytes: refused, naming the file.
mkdir "$work/copy"
cp *.bin "$work/copy/"
( cd "$work/copy" && ls -1 *.bin > "$work/copy.txt" )
( cd "$work/copy" && "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/copy.txt" \
    -s "$work/h.ledger" > "$work/h1.csv" 2>> "$work/log" )
cp scaled.bin "$work/copy/excerpt.bin"
if ( cd "$work/copy" && "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/copy.txt" \
        -s "$work/h.ledger" > "$work/h2.csv" 2> "$work/h2.err" ); then
    verdict="accepted"
else
    verdict="refused"
fi
check "a changed input is refused" "$verdict,$(rows "$work/h2.csv")" "refused,0"
check "and the message names it" "$(grep -c 'excerpt.bin changed' "$work/h2.err")" 1

# ---- an output that cannot be written ----
# A pair is done only once its row is out. When the output cannot take the
# row, the run stops and the ledger records no pair, so nothing is lost.
if "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/list.txt" -s "$work/i.ledger" \
        > /dev/full 2> "$work/i.err"; then
    verdict="exit 0"
else
    verdict="stopped"
fi
check "a full output device stops the run" "$verdict" stopped
check "and no pair is recorded as done" \
    "$(grep -v '^#' "$work/i.ledger" | grep -c . || true)" 0
# At least once: two threads can both find the device full before either
# has stopped the run, and each says so.
check "and the message says what could not be written" \
    "$(grep -q 'Cannot write the results' "$work/i.err" && echo said || echo silent)" said

# ---- an interruption ----
# 24 inputs, 276 pairs: a run killed part way through, then resumed. The two
# outputs joined and reduced by pair identity have to equal one uninterrupted
# run, and the ledger has to end complete. Where the kill lands is not
# controlled, and the check holds wherever it does, the run finishing first
# included: what is promised is no lost pair and a duplicate at most.
if command -v timeout >/dev/null 2>&1; then
    mkdir "$work/big"
    for prefix in p q r s; do
        for f in *.bin; do cp "$f" "$work/big/$prefix-$f"; done
    done
    ( cd "$work/big" && ls -1 *.bin > "$work/big.txt" )
    ( cd "$work/big" && "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/big.txt" \
        > "$work/big_full.csv" 2>> "$work/log" )
    ( cd "$work/big" && timeout -s KILL 0.3 "$bin" -f csv -m full -i 0 -k 1 -b 0.1 \
        -l "$work/big.txt" -s "$work/j.ledger" > "$work/j1.csv" 2>> "$work/log" ) || true
    ( cd "$work/big" && "$bin" -f csv -m full -i 0 -k 1 -b 0.1 -l "$work/big.txt" \
        -s "$work/j.ledger" > "$work/j2.csv" 2>> "$work/log" )
    # rows of both parts, one per pair, whichever part had it first
    { tail -n +2 "$work/j1.csv"; tail -n +2 "$work/j2.csv"; } | grep . \
        | awk -F, '!seen[$1 FS $2]++' | sort > "$work/joined.big"
    tail -n +2 "$work/big_full.csv" | sort > "$work/full.big"
    check "an interrupted run and its resume together equal one run, by pair" \
        "$(diff "$work/joined.big" "$work/full.big" > /dev/null && echo same || echo differs)" same
    check "and the ledger ends up holding every pair" \
        "$(grep -v '^#' "$work/j.ledger" | sort -u | grep -c .)" 276
    check "with no more than one duplicate row per pair across the two parts" \
        "$({ tail -n +2 "$work/j1.csv"; tail -n +2 "$work/j2.csv"; } | grep . \
            | cut -d, -f1,2 | sort | uniq -c | awk '$1 > 2' | grep -c . || true)" 0
    printf '  ..    (the kill landed after %s of %s rows)\n' \
        "$(rows "$work/j1.csv")" "$(rows "$work/big_full.csv")"
else
    printf '  ..    no timeout(1) here, the interruption check is skipped\n'
fi

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi
