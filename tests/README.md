# Tests

    make test          # unit tests, then both shell suites
    make unit          # unit tests alone, builds its own binary

    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/run.sh
    MPEG7DUPES=/usr/local/bin/mpeg7dupes sh tests/ledger.sh

`run.sh` compares the six fixtures against each other and checks the result two
ways. Named checks say what a given number means, so a failure names the
property that broke. A recorded copy of the whole output in `expected/` catches
everything the named checks do not think to ask about.

`ledger.sh` covers `-s`. Its failure mode is silent: a pair wrongly skipped
never appears in the output and nothing reports it, so every check there counts
rows against a known total instead of looking for an error. It caught one real
fault while being written, a ledger line loaded without ordering the pair
first, which broke resuming from a hand-edited file.

## unit/

Whatever the command line cannot reach. `tests/unit/harness.h` is forty lines
of assertion macros; a framework would mean a vendored file and a build step
for eighteen checks, which is not a trade worth making yet. Replace it when a
test needs fixtures, setup and teardown, or parameterised cases.

`test_ledger.c` covers the hash table. The one that earns its place is that the
table stays at most half full: `ledgerInsert` probes until it finds an empty
slot, so a table that ever filled would spin forever, and nothing else asserts
the sizing that prevents it.

`test_lookup.c` writes down how the two functions every comparison runs through
actually behave, because both differ from what their names and documented
defaults suggest and it took a long time to establish:

- `get_l1dist` reads nothing from the context but a table of ternary digit
  distances, so the frame distance is the same whatever the thresholds are.
  Every threshold applies after it. That is what allows one recorded run to be
  swept for thXh, thDi, thIt and minScore instead of comparing again per value.
- `get_jaccarddist` divides two popcounts as integers and the union is never
  smaller than the intersection, so the value is only ever 0 or 1 and cannot
  reach the documented defaults of 9000 and 60000. The coarse filter accepts
  every pair. Turned down to 1 it does fire, and it rejects the coarse
  signatures that agree, because the value is a similarity being tested as a
  distance.

Both functions are static, so `test_lookup.c` includes `signature_lookup.c`
instead of linking against it, and the Makefile keeps that source out of the
test binary's other half. The unit target compiles sources rather than reusing
`build/`, so running it cannot leave `-march=native` objects behind for a later
`make static` to pick up.

## The fixtures

Signatures, not videos, and they are checked in rather than generated. A
signature depends on how ffmpeg decoded and scaled the clip, so building them
during the test would make the expected output move with the ffmpeg version,
and the test would report the encoder changing as the comparison breaking. As
files, they pin the input and the test covers only the comparison code.

Each is 30 seconds sampled at 5 fps, so 150 frames and about 14 kB, from
synthetic sources. All six together are 66 kB.

| Fixture | What it is | Why it is here |
| --- | --- | --- |
| `base.bin` | 30s of one pattern | the reference the others are measured against |
| `scaled.bin` | `base` at half the width | the everyday re-encode |
| `excerpt.bin` | the middle 12s of `base` | an extract, matching end to end inside a longer clip |
| `headinsert.bin` | 10s of another pattern, then `excerpt` | shared content that starts partway in |
| `tailinsert.bin` | `excerpt`, then that same 10s | the same insert at the other end |
| `unrelated.bin` | a third pattern | shares nothing, so it sets the noise floor |

`headinsert` and `tailinsert` exist because their shared region sits inside both
files. Neither side reaches both of its own ends, so the code cannot settle the
match by running off the edges and has to choose a candidate on its merits.
That choice used to be inverted: `base` against `headinsert` scored 14, and 506
once it was fixed. Two named checks guard it.

`sh tests/make-fixtures.sh` rebuilds them and needs ffmpeg. Rerunning it
changes the signatures and therefore `expected/compare.csv`, so it is not part
of the suite. Regenerate only when you mean to.

## What is not covered

`headinsert` against `tailinsert` shares both the insert and the content. The
code stops searching at the first match that reaches both ends, so which of the
two it reports depends on the order candidates come up in. Here it lands on the
content, which is the answer we want, so this fixture set does not reproduce
that fault. The todo list describes it.
