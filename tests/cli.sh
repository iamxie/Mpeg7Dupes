#!/usr/bin/env sh
# Checks the command line contract: what the defaults are, which values are
# refused, how a list file and its paths are read, how names reach the CSV,
# and that a signature that cannot be read stops the run instead of passing
# as a pair that did not match.
#
#   sh tests/cli.sh
#   MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/cli.sh
#
# Every case builds its own inputs from the fixtures in a temporary directory,
# so nothing here depends on the working directory or touches the tree.

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

cp "$here"/fixtures/*.bin "$work"/
cd "$work"
ls -1 *.bin > list.txt

# Runs the binary with stdout in out.csv and stderr in err.txt, and prints the
# exit status instead of letting set -e act on it.
run() {
    if "$bin" "$@" > out.csv 2> err.txt; then echo 0; else echo $?; fi
}
rows() { tail -n +2 "$1" | grep -c . || true; }
count() { grep -c . "$1" || true; }
refused() { if [ "$1" -ne 0 ]; then echo refused; else echo accepted; fi; }

echo "Checking the command line with $bin"

# ---- defaults: csv, longest, no minimum length, no minimum score ----
# base against tailinsert tells longest from full: full settles on the wrong
# region at 18.40, and run.sh pins both answers. base against scaled at 150 of
# 150 frames says nothing ended the walk early.
st=$(run base.bin tailinsert.bin scaled.bin)
check "the defaults run with no flag at all" "$st" 0
check "and print csv" "$(head -1 out.csv | cut -d, -f1,16)" "First signature,whole"
check "and search in longest mode" \
    "$(awk -F, '$1 == "base.bin" && $2 == "tailinsert.bin" { print $12 "," $13 }' out.csv)" \
    "9.00,20.80"
check "and do not cut a match short" \
    "$(awk -F, '$1 == "base.bin" && $2 == "scaled.bin" { print $4 "," $16 }' out.csv)" \
    "150,1"
# The score counts votes for the alignment, not frames: on real pairs the old
# default of 49 hid 900-frame duplicates. Every candidate that matched prints.
# Since build 7 the coarse filter leaves the fixtures no noise rows in the
# default mode, so the rows under 49 that show the old default is gone are
# full's: two of its early stops score 14 and 5.
"$bin" -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > plain.csv
"$bin" -b 0.1 -k 1 -l list.txt 2>/dev/null | tail -n +2 | sort > k1.csv
check "and print every scoring row, the same as -k 1" \
    "$(cmp -s plain.csv k1.csv && echo same || echo differs)" same
"$bin" -m full -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > fullplain.csv
check "which includes rows scoring below the old default of 49" \
    "$([ "$(awk -F, '$3 < 49' fullplain.csv | grep -c . || true)" -gt 0 ] && echo yes || echo no)" yes

# ---- -i filters the finished walks and leaves the long ones alone ----
# In longest mode the reported candidate is the longest, so the rows of an
# -i N run have to be exactly the rows of the -i 0 run with N frames or more.
# Before build 4 every row came back exactly N frames long.
"$bin" -i 0 -k 1 -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > all.csv
"$bin" -i 70 -k 1 -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > i70.csv
awk -F, '$4 >= 70' all.csv > all_ge70.csv
check "-i 70 reports exactly the rows of the -i 0 run with 70 frames or more" \
    "$(cmp -s i70.csv all_ge70.csv && echo same || echo differs)" same
check "and that is fewer rows than -i 0, none of them 70 frames long" \
    "$([ "$(count i70.csv)" -lt "$(count all.csv)" ] \
        && [ "$(awk -F, '$4 == 70' i70.csv | grep -c . || true)" -eq 0 ] \
        && echo yes || echo no)" yes
"$bin" -i 100 -k 1 -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > i100.csv
check "-i 100 leaves the 150-frame match and nothing shorter" \
    "$(cut -d, -f4 i100.csv | sort -u | tr '\n' ' ')" "150 "

# ---- -b is a ratio and arrives in the core as one ----
# base against headinsert walks 62 frames and 60 of them are good, 0.968: 0.9
# has to keep it and 0.99 has to drop it. Whatever candidate a run does
# report has to clear the ratio the run was given. Before build 4 every value
# below 1 was truncated to 0 on the way in.
ratio_below() {
    awk -F, -v b="$1" 'NR > 1 { if ($5 / $6 < b) n++ } END { print n + 0 }' out.csv
}
st=$(run -k 1 -b 0.9 base.bin headinsert.bin)
check "-b 0.9 keeps 60 good frames in 62" \
    "$(awk -F, 'NR > 1 { print $5 "," $6 }' out.csv)" "60,62"
st=$(run -k 1 -b 0.99 base.bin headinsert.bin)
check "-b 0.99 does not report that candidate" \
    "$(awk -F, 'NR > 1 && $5 == 60 && $6 == 62' out.csv | grep -c . || true)" 0
check "and whatever it reports clears 0.99" "$(ratio_below 0.99)" 0
# Over the whole list: at -b 0.1 two pairs are reported on walks with 60 good
# frames in 63, 0.952; at -b 0.96 those walks are refused and whatever is
# reported instead clears the ratio. A refused walk can be replaced by
# another candidate for the same pair, so the rows are not compared as sets.
"$bin" -i 0 -k 1 -b 0.96 -l list.txt 2>/dev/null > out.csv
check "-b 0.96 over the fixtures reports only rows that clear it" "$(ratio_below 0.96)" 0
check "and not the two walks with 60 good frames in 63" \
    "$(awk -F, 'NR > 1 && $5 == 60 && $6 == 63' out.csv | grep -c . || true)" 0
check "which -b 0.1 does report" \
    "$(awk -F, '$5 == 60 && $6 == 63' all.csv | grep -c . || true)" 2

# ---- the same answer whatever the thread count or the order ----
# The outer loop is parallel and nothing promises the order of the rows, but
# the content of each row has to be the same at -j 1 and on every core, and
# with the two files the other way round: matched frames, the boundaries of
# each side and the mean distance. Which side is first, and so score and
# offset, may differ; the substance may not.
"$bin" -j 1 -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > j1.csv
"$bin" -b 0.1 -l list.txt 2>/dev/null | tail -n +2 | sort > jall.csv
check "-j 1 and every core report the same rows" \
    "$(cmp -s j1.csv jall.csv && echo same || echo differs)" same
substance() {
    # pair in fixed order, matchframes, goodframes, totalframes, meandist and
    # the two boundary spans, each attached to its own file
    awk -F, 'NR > 1 { a = $1; b = $2; sa = $12 "-" $13; sb = $14 "-" $15;
        if (a > b) { t = a; a = b; b = t; t = sa; sa = sb; sb = t }
        print a "," b "," $4 "," $5 "," $6 "," $9 "," sa "," sb }' "$1" | sort
}
st=$(run -b 0.1 base.bin headinsert.bin excerpt.bin)
substance out.csv > ab.txt
st=$(run -b 0.1 excerpt.bin headinsert.bin base.bin)
substance out.csv > ba.txt
check "and the files in the other order give the same matches" \
    "$(cmp -s ab.txt ba.txt && echo same || echo differs)" same
check "with three rows to compare" "$(grep -c . ab.txt || true)" 3

# ---- values the options must refuse ----
# Each of these used to be accepted: atof made "abc" 0, "0.5x" 0.5 and "1.5"
# 1.5 on an option that could not hold it, and every run went to completion.
for bad in "-b nan" "-b inf" "-b 1.5" "-b -0.1" "-b abc" "-b 0.5x" \
           "-i 1.5" "-i -1" "-i 1e3" "-x 12abc" "-x -5" "-d 1.5" "-c abc" \
           "-k 0" "-k 1.5" "-j abc" "-j -1" "-m bogus" "-m csv" "-f bogus" \
           "-f full" "-f beautiful"; do
    # shellcheck disable=SC2086
    st=$(run $bad base.bin scaled.bin)
    check "$bad is refused" "$(refused "$st")" refused
    check "and the message names the option" \
        "$(grep -q -- "${bad%% *}" err.txt && echo named || echo unnamed)" named
done
st=$(run -b "" base.bin scaled.bin)
check "an empty -b is refused" "$(refused "$st")" refused

# ---- list files ----
printf 'base.bin\nscaled.bin' > nonl.txt
st=$(run -k 1 -l nonl.txt)
check "a list whose last line has no newline keeps that line" "$st,$(rows out.csv)" "0,1"
printf 'base.bin\r\nscaled.bin\r\n' > crlf.txt
st=$(run -k 1 -l crlf.txt)
check "CRLF line endings are stripped" "$st,$(rows out.csv)" "0,1"
printf '\nbase.bin\n\n \t\nscaled.bin\n\n' > blank.txt
st=$(run -k 1 -l blank.txt)
check "blank lines are skipped" "$st,$(rows out.csv)" "0,1"
printf 'base.bin\n\n' > one.txt
st=$(run -k 1 -l one.txt)
check "a list with one entry is refused" "$(refused "$st")" refused
# find_reuse.py passes the candidates as -l and the source as -n, and a folder
# holding one video used to fail the whole run: two entries were required in
# -l alone, whatever -n added. What matters is two files in all.
printf 'scaled.bin\n' > onen.txt
st=$(run -k 1 -l one.txt -n onen.txt)
check "one entry in -l and one in -n compare as a pair" "$st,$(rows out.csv)" "0,1"
: > none.txt
st=$(run -k 1 -l none.txt -n nonl.txt)
check "an empty -l is still refused, whatever -n holds" "$(refused "$st")" refused
printf 'base.bin\nmissing.bin\n' > missing.txt
st=$(run -k 1 -l missing.txt)
check "a missing file stops the run and is named" \
    "$(refused "$st"),$(grep -q missing.bin err.txt && echo named || echo unnamed)" \
    "refused,named"

# ---- names with a comma, quotes, spaces and non-ASCII characters ----
mkdir odd
cp base.bin 'odd/a, "b".bin'
cp scaled.bin 'odd/中文 檔.bin'
printf 'odd/a, "b".bin\nodd/中文 檔.bin\n' > odd.txt
st=$(run -k 1 -l odd.txt)
check "names with a comma, quotes, spaces and CJK compare" "$st,$(rows out.csv)" "0,1"
check "the one with a comma and quotes is quoted with its quotes doubled, the other left alone" \
    "$(grep -c -F '"odd/a, ""b"".bin",odd/中文 檔.bin,1940,' out.csv || true)" 1

# ---- a path too long for the table is refused, not cut ----
seg=$(printf 'd%.0s' $(seq 1 100))
mkdir -p "$seg/$seg/$seg/$seg"
cp base.bin "$seg/$seg/$seg/$seg/base.bin"
long="$seg/$seg/$seg/$seg/base.bin"
printf '%s\nscaled.bin\n' "$long" > long.txt
st=$(run -k 1 -l long.txt)
check "a list entry of 412 bytes is refused" "$(refused "$st")" refused
check "and the message says which line" "$(grep -c 'long.txt line 1' err.txt || true)" 1
st=$(run -k 1 "$long" scaled.bin)
check "and so is the same path on the command line" "$(refused "$st")" refused

# ---- signatures that cannot be read stop the run ----
# Each used to be a segfault, which is a non-zero exit with no file named in
# it, and under a ledger the pair would then be retried forever.
: > empty.bin
head -c 5000 base.bin > truncated.bin
head -c 20 base.bin > header.bin
printf 'not a signature' > garbage.bin
head -c 300 /dev/urandom > random.bin
for f in empty.bin truncated.bin header.bin garbage.bin random.bin; do
    st=$(run -k 1 base.bin "$f")
    check "$f stops the run" "$(refused "$st")" refused
    check "and the message names it" \
        "$(grep -q "$f" err.txt && echo named || echo unnamed)" named
    check "and no row is printed for it" "$(rows out.csv)" 0
done

echo
if [ "$failures" -eq 0 ]; then
    echo "$checks checks, all passed"
else
    echo "$checks checks, $failures failed"
    exit 1
fi
