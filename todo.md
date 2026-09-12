# TODO

In priority order. Finished work belongs in README.md, benchmark.md and the
commit history. Measurements live in benchmark.md; this file states what
remains and what completion requires.

## A third independent validation set

Use new original sources and series, excluding the tuning material, earlier
validation sets and all detector development footage. Cover still/slides and
locked-off shots with bars, plain dark/landscape footage with short quotes,
native portrait and rotated video, blurred reframes, pillarboxing, shared
openings across a series, and speed changes including off-grid ratios.
Motion and the now available opt-in black mode must be declared before
looking at results. Never treat a synthetic regression as this validation.

Done when: freeze code, parameters, source selection and truth before one
formal run; report both questions per scenario and original source, including
failed/unprocessed inputs and crop decisions. For Q1 separate same-origin
truth from longest-shared-span coverage truth. Keep alignment error separate
from partial coverage. If results lead to tuning, retain the original run,
reclassify that set as development material and use new sources next time.

## Speed-adjusted source coverage (low; separate behaviour change)

`matchframes` counts walk steps; it is not a reliable count of source frames
used at another speed. Source and candidate spans are already recorded.
Ranking candidates at different ratios remains unchanged by decision.

Done when: define source coverage, inclusive endpoints and tolerated bad
frames; test full and partial reuse in both directions at 0.8x, 1.25x and
off-grid speeds; compare the old corpus's threshold decisions; version the
record/behaviour change. Preserve the grid/low-motion position limitations.

## A standalone signature verification command (low)

The loader rejects unsupported or malformed signatures when comparison reaches
them. Add directory/list verification and optional comparison preflight using
the same complete format checks. `sigstore.read_header()` only checks the
supported header and expected size; it is not full validation.

Done when: report every bad file with its reason and a failing exit status,
accept valid short files, cover bad flags/values/ranges/truncation, and detect
bad inputs before comparison starts. Measure cost; do not promise a fixed
per-file time or copy only the cheap header check into another implementation.

## A warning detector for blurred-background reframes (low)

Orientation alone cannot distinguish a native portrait video from a horizontal
picture over a blurred enlargement. Reframes remain unsupported. Investigate
whether a sharp central band between moving soft bands reliably distinguishes
these cases, with native portrait negatives and regenerated reframe positives.

Done when: measured separation justifies a detector and the CLI, JSON and
HTML show its warning, with synthetic regression coverage. If the evidence
does not separate them, keep the existing general limitation instead.

## Replace unused vendored ffmpeg headers (low; independent change)

Inventory the reader/helpers actually used and preserve required attribution.
The executable uses no libav symbols, but imports internal headers for the
bit reader, helpers, padding constants and small types. Remove unused types
and includes without deleting the tree blindly. A replacement reader must
handle unsigned fields above 2^31 and reject reads beyond the buffer without
relying on padding; implement it from the format, not a copied ffmpeg reader.

Done when: only necessary owned headers and helpers remain; release, static,
ASan and real-ffmpeg tests pass; unsigned/end-of-buffer cases pass; valid
retained signature comparisons match the recorded baseline. Keep this diff
separate from detector features and output changes.

## Native Windows build (lowest; outside the second review delivery)

The earlier static assessment is in commit `aaf7fa9`. Reassess GNU argp,
OpenMP support, POSIX file/stream calls and the vendored headers against the
current tree. New locking and temporary-file contracts must remain intact.

Done when: a MinGW-w64 build passes the C suites and the Python/ledger/cache
contracts are tested on Windows. An old static assessment is not a build test.

## Not planned

- Loading the whole signature library into a cache: prior measurements did
  not justify it; single-source scheduling shares only its current source.
- Tuning short-source false matches: the owner chose to document the risk;
  stricter thresholds also lost real matches. See benchmark.md.
- Changing how different-speed candidates are ranked, or listing every
  separate reuse: the result remains the longest contiguous match per pair.
- Signature generation code: ffmpeg generates signatures; this project reads them.
- Rebuilding the index from signatures: applied crop and duration live in the
  index. Keep it with the signature directory, or regenerate the cache.
- Restoring `beautiful` output: removed; the HTML report is the readable view.
