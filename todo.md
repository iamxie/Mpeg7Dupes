# TODO

In the order worth doing them. Each entry says what it is, what is known,
and what done looks like. Finished work is not kept here: benchmark.md and
README.md hold what was learned, and the git log holds the rest.

## 1. A third independent validation set

Nothing waits on it now: the false matches of short sources were left as
they are and stated in README.md, and bars on still footage moved to a mode
the user chooses, item 8. It is wanted before any setting changes again, and
to check the limits the second set wrote down. Material, from sources used
in none of the sets: still footage, slides or a locked-off camera, with bars
added, to try item 8 on when it exists; night skies, landscapes and other
plain footage cut to sources of 10 s to two minutes, to check the
short-source limit README.md states; clips under a minute, which the second
set meant to have and did not, its news clips running 66 to 173 s; vertical
originals, and 4:3 sources pillarboxed at the sides, which the detector does
not look for; and more than two episodes of one series, whose five minute
cuts, sharing the title sequence and an advertisement put in front, reached
37.8 per cent on the second set, 2.2 points under the line. The treatments
stay as in the second set.

Done when: with every setting frozen, the set is run once and reported per
scenario and for both questions in its own benchmark.md section, and the
detector's decision is checked against the truth for every file.

## 2. Remove the fast and full modes

`-m fast` stops at the first candidate that qualifies and `-m full` stops
at the first whose walk reaches both ends. Both exist to save time, and
stopping early is exactly what makes them wrong: on the fixtures `full`
settles on a ten-frame candidate at an extreme speed ratio, on the
benchmark it recovered fewer of the true duplicates than `longest`, see
benchmark.md, and the coarse filter of build 7 has already taken two thirds
off the time they were meant to save. Decided on 2026-09-10. A quick mode,
if one is ever wanted, gets defined from a need, such as stopping at the
first candidate over a coverage threshold, not inherited from these.

Done when: `-m` accepts only `longest`, the way `-f` accepts only `csv`,
and `-m full` is refused with a message naming the option; `MODE_FAST`,
`MODE_FULL` and the branches in `evaluate_parameters` and
`lookup_signatures` that served them are gone; `tests/run.sh` runs
`longest` only and `expected/compare.csv` goes with the mode, leaving
`compare-longest.csv`; `tests/ledger.sh` and `tests/cli.sh` run `longest`
with their expectations re-recorded; the README's `-m` row and
`tests/README.md` say so; benchmark.md keeps the `full` against `longest`
comparison as history, marked with the build that removed it. The root
scripts `bench/sweep.sh` and `bench/run-plan.sh` mention `-m full` in a
comment and two superseded runs and can stay as they are.

## 3. A verify command that checks every signature in a directory

An empty, truncated or malformed signature stops a run naming the file,
but only when its turn comes, so in a large set a bad file is found late:
with `-s` nothing done is lost, without it the run has to start over. What
is wanted is a check that can be run on its own, over a whole directory,
before any comparison: walk the directory, read each file's header, and
apply the rule the loader and `sigstore.read_header` already apply, that
the coarse and fine counts in the header have to be backed by the bytes in
the file. About a millisecond a file.

Done when: one command lists every file that is not a complete signature,
with the reason, and exits 1 if there was any, 0 otherwise; a directory of
good signatures with an empty, a truncated and a random file added names
exactly those three; and the comparison can run the same check over its
list before starting, so a bad file fails the run in seconds rather than
hours.

## 4. Load each signature once instead of once per comparison

Every pair re-reads and re-parses the second signature from disk, so a file
is parsed once for each comparison it takes part in; with 600 inputs that
is roughly 600 parses per file. Done when: the read and parse time, the
comparison time and the peak memory are measured separately on the
regression set; a cache with a memory ceiling, or loading in batches, is
chosen on those numbers rather than preloading the whole library; the
results are unchanged row for row; and the ceiling holds on a set larger
than memory. The outer-loop parallelism stays as it is unless a measurement
says otherwise.

## 5. Coverage at a speed ratio other than 1.0 counts the slower clip's frames

Since build 7 a copy at another speed is found and placed: a 1.25x and a
0.8x copy of a 60 s synthetic clip both come back whole, with the ratio
voted to the nearest thirtieth. `matchframes` counts the frames of the
slower clip, so `tools/find_reuse.py`, which divides by the source's frame
count, reads 80 per cent for a 1.25x copy that holds all of the source, and
a position on the faster side can be off by the length of the match times
the gap between the true ratio and the grid: the 0.8x copy was placed 2.6 s
in where 0 is right. The output says so on the line. On real footage, the
validation set's six 1.25x copies were all found and placed exactly, the
ratio voted at 0.80, which is on the grid, and all six read 80 per cent.
The second set's ten did the same. A 10 or 20 s clip inside a 1.25x copy
often did not: over both sets 13 of 29 were voted at 1.0, because the
longest walk wins and at 1.0 a clip is walked in more steps. Decided on
2026-09-12 to leave that as it is, since the clip is still found and the
start is off by at most a fifth of the clip; README.md states it under
Limits, and changing how walks at different ratios are ranked is not
planned.
Done when: the coverage is converted to the source's frames using the
ratio, or the source-side span is used instead, with a test on a
speed-changed stand-in; and the grid error is either accepted in the
record's limits or the ratio is refined from the walk.

## 6. Native Windows build

Assessed on 2026-09-08 at commit `5ceaf19` by static analysis only and
then shelved; commit `aaf7fa9` holds the full assessment. What blocks it,
largest first: `<argp.h>` is GNU only and used shallowly, so argp-standalone
or about 200 lines replace it; `#pragma omp atomic capture` is OpenMP 3.1,
which MinGW-w64 handles and MSVC's `/openmp` does not; about 25 lines of
POSIX calls (`dup`, `dup2`, `mkdir` with a mode, `__attribute__((optimize))`);
and the libav headers, which are a dependency rather than a change. No
inline assembly, no pthread, no signals, no directory walking; slog already
supports Windows. Done when: it compiles under MinGW-w64 and the C suites
pass there, with `tests/run.sh`, `ledger.sh` and `cli.sh` either running
under a Windows shell or rewritten in C.

## 7. Tell a reframe with a blurred backdrop when one is a candidate (low)

Low priority. Vertical reframes are not supported, decided on 2026-09-11:
a 16:9 video made into 9:16, the picture across the middle over a blurred
enlargement of itself, keeps too little of the picture for the comparison,
and on the second set such copies were found in 63 of 106 same-video pairs
and 14 of 27 contains-my-clip matches. README.md says so under Limits. What
is wanted is not support but a message: when a candidate is recognised as
this kind of video, tell the user it keeps too little of the picture to be
matched, so a miss against it is not read as a clean result. The layout has
a signature of its own: a sharp band across the middle, between two soft
bands whose content still moves, with the band's edges at the same rows in
every frame. Portrait orientation alone does not tell it, since a video shot
upright is portrait too; neither validation set has one of those to check
against.

Done when: a detector, sampling a few frames the way `tools/detect_bars.py`
does, says whether a video has a sharp middle band between blurred ones; it
is measured on the second set's reframes and on videos shot upright, and
fires on the first and not the second; `tools/find_reuse.py` reports the
candidates it fires on with that message, in the output, the JSON record and
on the page; and a unit test pins a synthetic reframe and an upright video.
If no measure separates them, README.md's limit stays the only warning.

## 8. A second way to find bars, by their colour, for the user to choose (low)

Low priority. The bar detector finds bars by what does not move, and that
stays the default: it finds bars with lettering in them, where a test for
black stops at the first line of text, and it leaves a dark picture alone.
On a video that barely moves at all, a slide talk say, it cannot tell bars
from picture and crops nothing, so a copy with bars added may not be found
at all; README.md says so under Limits. What is wanted is a second mode that
finds bars by their colour, rows at the black level across the width in
every sample, counted from the edge inwards, which a user who meets that
case can choose on purpose. It is never chosen automatically.

Done when: `tools/detect_bars.py` and `tools/find_reuse.py` take an option
that selects the mode, the motion mode staying the default; the mode is part
of a signature's identity in the store, so signatures cropped the two ways
are never mixed, and the JSON record and the page say which mode a run used;
with the mode on, the second validation set's slide talk has its barred
copies found; a unit test pins a still shot with black bars in both modes;
and README.md's limit on slide talks names the option.

## Not planned

- **Tuning "contains my clip" for short sources.** On the second validation
  set and a length test on its sources, a source under about two minutes
  whose picture has a common light layout was reported inside unrelated
  footage of a similar layout, in up to 10 per cent of the unrelated
  candidates; other material almost never was. Every setting that removed
  those matches, a stricter `-x` or a `meandist` ceiling, also lost
  reframed copies and copies cropped differently, and the ceiling that
  cleared the set left 16 of 236 false matches in the length test. Decided
  on 2026-09-11 not to tune for it: README.md states it under Limits and
  benchmark.md has the measurements.
- **Verifying, or keeping, signature generation code.** There is none.
  Signatures come from ffmpeg's `signature` filter and this repository only
  reads them; the entry that used to sit last here came from the original
  author's todo and referred to code that was never in the tree. Removed on
  2026-09-10.
- **Listing every separate reuse of a source.** The comparison keeps one
  match per pair, the longest contiguous run, and that is the scope: the
  program says whether a source was used and where its longest use sits.
  README.md says so under Limits.
- **Rebuilding the index from the signature directory.** The store is a
  cache. The crop applied and the duration live only in the index, so losing
  it costs one re-decode of everything and nothing else; keep a copy of the
  index with the directory.
- **The `beautiful` output.** Removed in build 8. It drew a tree of
  box-drawing characters that assumed rows arrive in order, which the
  parallel loop does not promise; `tools/render_report.py` is the readable
  view.
