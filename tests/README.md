# Tests

    make test          # builds bin/mpeg7Dupes.elf, then the unit tests and the suites
    make test STATIC=1 # the same, building and testing the static binary
    make test DEBUG=1  # the same under AddressSanitizer
    make unit          # unit tests alone, builds its own binary
    make smoke         # real ffmpeg: pipeline/cache plus input format and geometry

    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/run.sh
    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/ledger.sh
    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/cli.sh
    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/smoke.sh
    sh tests/tools.sh  # the Python tools, no binary needed

`make test` needs no ffmpeg and takes a few seconds after the build. CI runs
`make test` and `make smoke` on every push and pull request, on x86_64 and
aarch64, and the release workflow runs both again on the static binary it is
about to publish. Nothing in CI runs the video benchmark; that takes hours on
private material and lives in `benchmark.md`.

## Four layers, and what each is for

**Unit tests** (`unit/`, C and Python) reach what the command line cannot:
the hash table under the ledger, the option value parsers, the two inner
functions every comparison runs through, constructed candidates walked
through `evaluate_parameters`, and the signature store's decisions about when
a signature may be reused. They are where a number or a state goes wrong.

**Fixture regression** (`run.sh`) compares six checked-in signatures and
checks the result against named properties and the recorded
`compare-longest.csv` output. It protects the comparison's results
without depending on how the ffmpeg on the machine happens to encode.

**Mock integration** (`cli.sh`, `ledger.sh`, `unit/test_sigmake.py`,
`unit/test_find_reuse.py`, and the root `test_make_signatures.py` outside
this repository) runs the real programs against stand-ins for ffmpeg,
ffprobe and, for the Python tools, mpeg7dupes itself, so that the cache, the
failure paths and the shape of the data crossing between modules are tested
without decoding anything.

**Real-tool integration** (`smoke.sh`, `video_io.py`) is deliberately small: three
synthetic clips through ffmpeg, the store and the binary via
`tools/find_reuse.py`, twice. It is the only place the real pipeline runs,
and what it catches is wiring: the signature format drifting, a filter
argument ffmpeg no longer accepts, the cache failing to recognise its own
files. Input checks also cover real single-frame, two-frame and short signatures, selection of the
first video track and autorotated dimensions. They say nothing about accuracy
beyond these small cases.

Not every function has a mirror test. A behaviour change or a fixed defect
gets a test that would have failed before it; the rest is covered by the
observable results above.

## Tests that used to pin a known limitation

Nothing here records behaviour kept as it is rather than as it should be
any more. Two things used to be pinned that way.

`run.sh` and `expected/compare.csv` recorded `-m full`, a mode with a known
limitation: it stopped at the first candidate that reached an end in each
file, which on `base` against `tailinsert` was the wrong region and, from
build 7, on two other pairs a handful of frames at an extreme ratio. Build
10 removed `full` and `fast`, and `compare.csv` with them;
`expected/compare-longest.csv` is the one recorded copy, and the check on
`base` against `tailinsert` now states the intended region.

The coarse filter used to be pinned here as a known defect, an integer
division that never reached its thresholds. Build 7 fixed it, and the
checks in `unit/test_lookup.c` now state the intended behaviour: identical
words at distance 0, words that share nothing at 10000, and the defaults
rejecting a pair only when three of its five words share a tenth or less.

When a limitation is lifted, update the expectation from the ground truth
and let the recorded copy change with it.

## The suites

`run.sh` compares the six fixtures against each other in both modes and
checks the result two ways. Named checks say what a given number means, so a
failure names the property that broke: that a re-encode covers the whole
clip, that an extract is found end to end, that the endpoints locate the
extract inside a longer clip, that the two clips sharing content inside both
files are scored as a real match rather than noise, and that `unrelated`
stays under the noise floor. The recorded copies catch everything the named
checks do not think to ask about. `UPDATE=1` rewrites them; do that only when
the change is understood.

`ledger.sh` covers `-s`. Its failure mode is silent: a pair wrongly skipped
never appears in the output and nothing reports it, so every check counts
rows against a known total instead of looking for an error. It covers the run
record too: a fresh ledger carries one with an identity line per input; other
settings, another binary and another build are refused with nothing compared;
a hand-seeded ledger without a record is reused with a warning and given one;
a new input adds exactly its own pairs; a changed input is refused by name; an
output that cannot be written stops the run before any pair is recorded; and
a run killed at an arbitrary point and resumed, joined and reduced by pair,
equals one uninterrupted run with the ledger complete. Where the kill lands
is printed, not controlled, and the check holds wherever it does.

`cli.sh` covers the command line contract, and every case in it was wrong
first: the defaults; that `-i` filters short candidates and leaves long ones
whole; that `-b` reaches the core as the ratio it was given; that `-j 1` and
every core report the same rows, and the two files in the other order the
same matches; the values each option refuses; list files without a final newline, with CRLF endings or
blank lines; names with commas, quotes, spaces and CJK, quoted in the CSV
only when they need it; paths too long for the table, refused rather than
cut; and signature files that are empty, truncated or random, which stop the
run naming the file. Under `make test DEBUG=1` the malformed files go through
the loader under AddressSanitizer.

`smoke.sh` makes a source, a copy of it with the head cut off and scaled
down, and an unrelated clip, and runs `tools/find_reuse.py` over them twice
with ffmpeg and ffprobe replaced by stand-ins that log each call and hand it
on. The first run has to fingerprint all three, report the copy as holding
the source at the same speed, place it at the head of the copy and five
seconds into the source, and leave the unrelated clip checked and unmatched.
The second run has to give the same numbers, run ffprobe not at all, and
call ffmpeg only to ask its version. Skips itself where there is no ffmpeg;
`REQUIRE_FFMPEG=1` makes that a failure, which CI sets.

`tools.sh` runs the Python suites under uv, or a bare interpreter, and skips
itself where there is neither; `REQUIRE_PYTHON=1` makes that a failure,
which CI sets.

## unit/

`harness.h` is a few assertion macros; a framework would mean a vendored
file and a build step for what is here. Replace it when a test needs
fixtures, setup and teardown, or parameterised cases.

`test_ledger.c` covers the hash table, above all that it stays at most half
full, since `ledgerInsert` probes until it finds an empty slot and a table
that ever filled would spin forever; and the run record and input digests
without a binary in the way.

`test_args.c` covers the two option value parsers: whole numbers with a
range and ratios in `[0, 1]`, and what they refuse.

`test_lookup.c` covers the functions every comparison runs through, all of
them static, so the file includes `signature_lookup.c` rather than linking
against it. The frame distance reads nothing from the context but a table,
so every threshold applies after it, which is what lets one recorded run
be swept for `-x`, `-i`, `-b` and `-k` instead of comparing again per
value. The coarse filter's Jaccard distance is checked at its scale and in
its direction. `suiteEvaluate` walks constructed candidates through
`evaluate_parameters`: two streams of identical frames with chosen frames
spoiled, so the ratio of good frames is known exactly, and `-b` and `-i`
are shown to do what their documentation says. Three suites were added
with build 7, each of which failed on build 6: `suiteWalk` plays one
stream at 0.8x and at 1.25x of the other, with frames whose distance is
exactly their difference in position, and requires the walk to cover the
slower clip with every frame good from the start, from the end and from
the middle; `suiteHough` delays one stream by twenty frames and requires
the alignment to be proposed at ratio 1.0 and offset +20, where the
candidate scan used to stop at the middle of the accumulator and only
negative offsets were ever proposed; `suiteTie` gives `longest` two
candidates of the same length and requires the closer one to win whichever
comes first.

`test_sigstore.py` covers when a signature may be reused and when it must
not be: an unchanged file is recognised from a stat, a changed one is not,
including one rewritten at the same size within the same second; two paths
can share one content; a retuned detector retires exactly the cropped
signatures. A row in the index is not enough on its own, the file has to be
there and be a whole signature by its header and size. A schema 1 index is
migrated in place.

`test_sigmake.py` covers making one signature with ffmpeg and ffprobe
replaced by the stand-ins in `fakesig.py`: a temporary name and a rename
into place, so a failed, truncated or empty result leaves nothing behind; a
second call reuses the store without running ffmpeg; a renamed copy reuses
it too; a failed `--overwrite` keeps the old file and row; the bar decision's
four states against a stubbed detector, the unreadable one failing the file
rather than recording a signature with no bars; and the detector being
handed the caller's ffmpeg and ffprobe.

`test_find_reuse.py` runs `find_reuse.py` as a whole program against the
same stand-ins plus one for `mpeg7dupes`, and covers the contract of the
command: every requested file in the record with a status, a file that
cannot be read recorded with the stage and reason and exit status 1, a run
with no match complete at 0, a candidate that is a source skipped, two
copies of a source both reported, an overrun keeping its raw numbers, a
speed ratio other than 1.0 flagged, a failing `mpeg7dupes` at 2, the coarse
filter left on by default and turned off by `--no-coarse-filter` with
`-d 10001`, the record and the page saying which way the run was made, and
the page rendered from the record saying what the terminal said. The seek that
parks each player was checked by hand in a browser once, since only a
browser can say whether the script runs; it is not automated, and the page
has to be served with HTTP Range support or opened from disk for it to
work.

`test_detect_bars.py` runs the bar detector's `analyse()` on frames drawn
by hand, with ffprobe and ffmpeg replaced, and pins the case version 1 got
wrong: a still shot with a moving clip in one sampling window, whose edges
hold still but show something else, has no bars. Bars on moving picture,
bars on a still shot with a moving advertisement, lettering inside a bar,
and footage that is still throughout keep the answers they had.

`test_tools.py` covers the pure functions behind `--json` and the HTML
report: the phrasing of a position, the shape of the record, what goes in
each cell, and that the player is seeked by attribute and script rather than
by the `#t=` fragment alone, which Chromium ignores on an element that
preloads only metadata.

## The fixtures

Signatures, not videos, and checked in rather than generated, because a
signature depends on how ffmpeg decoded and scaled the clip, so building
them during the test would make the expected output move with the ffmpeg
version and the test would report the encoder changing as the comparison
breaking. Each is 30 seconds sampled at 5 fps, 150 frames, from synthetic
sources; `make-fixtures.sh` has the recipe.

| Fixture | What it is | Why it is here |
| --- | --- | --- |
| `base.bin` | 30s of one pattern | the reference the others are measured against |
| `scaled.bin` | `base` at half the width | the everyday re-encode |
| `excerpt.bin` | the middle 12s of `base` | an extract, matching end to end inside a longer clip |
| `headinsert.bin` | 10s of another pattern, then `excerpt` | shared content that starts partway in |
| `tailinsert.bin` | `excerpt`, then that same 10s | the same insert at the other end |
| `unrelated.bin` | a third pattern | shares nothing, so it sets the noise floor |

`headinsert` and `tailinsert` exist because their shared region sits inside
both files: neither side reaches both of its own ends, so the code cannot
settle the match by running off the edges and has to choose a candidate on
its merits. That choice used to be inverted, and two named checks guard it.

What the recorded copy was made with: the fixtures on 2026-09-06, with the
ffmpeg then on PATH, whose version was not written down; the copy with build
7, and build 10 gives the same rows. Up to build 9 there was a second copy,
`compare.csv`, of `-m full`; it went with the mode. Rerunning
`make-fixtures.sh` changes the signatures and therefore the recorded copy,
so it is not part of the suite. Regenerate only when you mean to, and record
the ffmpeg version when you do.

## What is not covered

Accuracy on real footage is not covered by any test here; `benchmark.md` is
the measurement, and it says what it does and does not tell you.

## Build 11 regression coverage

`test_loader.py` runs the selected binary on single/two-frame signatures,
unsigned timestamps, unsupported flags, bad frame ranges and packed ternary
values, and oversized input. `test_ledger_cli.py` covers ambiguous names,
close floating-point settings, malformed/truncated records, locking and
ordinary quoted/Unicode paths. `DEBUG=1` now instruments the C unit binary
as well as the application with AddressSanitizer.

`test_p1_cache.py`, `test_p1_scan.py` and `test_p1_concurrency.py` exercise
source mutation, immutable publication, failed sampling, exact fps keys,
legacy collisions, invalid settings/CSV, complete failure inventories and
atomic JSON writes. Independent processes synchronize at barriers to test
cold database initialization, sharing one signature and isolating scan lists.
The root producer tests remain outside this public repository.
