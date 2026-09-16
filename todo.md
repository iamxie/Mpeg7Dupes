# TODO

In priority order. Finished work belongs in README.md, benchmark.md and the
commit history. Measurements live in benchmark.md; this file states what
remains and what completion requires.

## Conservative crop decisions on short and dark footage

The third independent validation is complete; its frozen methods, results and
limits are in benchmark.md. It exposed visible sky removed by motion and dark
dock picture removed by black. Successful matching does not establish a safe
crop when both sides lose the same picture. Keep motion as the default and
black explicit; do not choose a mode per pair after seeing its match score.

Done when: reproduce these failure mechanisms with focused pixel fixtures,
define when to abstain, preserve the producer failure/cache identity contracts,
and validate any changed detector on new original sources and series. If the
third-set footage informs a fix or tuning, record its transition to development
data and retain the original measurements. Do not absorb the separate short
source threshold or speed-ranking decisions into this work.

## Independently validate the optional fixed crop fallback

The explicit full-frame / fixed-5% cross-view retry is implemented and keeps
new hits review-only. It does not switch between motion and black detectors.
The third-set footage and direct-filter crop probe now serve as development
data for this feature; the earlier frozen validation remains historical evidence.

Done when: evaluate fresh sources and series with positive crops, unrelated
negatives, static/dark/short footage and position truth; separately measure
recovered misses, added false positives, and cost. Keep this opt-in and
review-only unless those measurements justify a new decision. Additional crop
amounts and shared-decode extraction need their own cost/benefit evidence.

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
