# Benchmark

What settings to use, and the measurements behind them.

Everything here was measured on one corpus of 96 videos built from 6 sources,
compared as all 4560 pairs, with `mpeg7dupes v0.1 b2`. Section
[Reproducing](#reproducing) has the commands. Section
[What this does not tell you](#what-this-does-not-tell-you) has the limits.

## The answer

Crop any bars off the top and bottom first, then

```sh
mpeg7dupes -f csv -i 0 -b 0.1 -k 1 -x 290 -m longest -l list.txt
```

and keep pairs whose **coverage** is at least 40 per cent, where

```
coverage = matchframes / min(frames in file A, frames in file B)
```

Over 4560 pairs this finds **720 of 720 true duplicates with zero false
positives**, with 13.9 points between the weakest duplicate and the strongest
non-duplicate. `tools/find_reuse.py` does all of it, cropping included.

Skipping the crop costs three of the 720 and, worse, leaves the classes
overlapping, so no threshold separates them cleanly. See
[Cropping the bars](#cropping-the-bars).

Each flag, and why:

| Flag | Value | Reason |
| --- | --- | --- |
| `-i` | `0` | Any other value truncates the match. `thDi` is both "minimum match length" and "stop once you have this many frames", and the second meaning corrupts the first. See README, *Known problems*. |
| `-b` | `0.1` | thIt. Held loose so nothing is dropped during the search. Coverage does the filtering afterwards, and does it better. |
| `-k` | `1` | minScore. Same reason. |
| `-x` | `290` | The last threshold before black bars start matching unrelated videos to each other. This is the interesting one; see [The threshold trade-off](#the-threshold-trade-off). |
| `-m` | `longest` | `full` stops the search too early on a common real-world layout and loses 17 per cent of true duplicates. See [Mode](#mode-longest-beats-full). |
| coverage | `≥ 0.40` | Sits in the empty band between pairs that share only an advertisement (34.1% at most) and genuine duplicates (48.0% at least, for the tightest legitimate case). |
| crop | before all of it | Bars are a fifth of the frame and identical in every video carrying them, and they shift the picture inside the frame so a copy with bars and one without stop lining up. `tools/detect_bars.py` finds them. |

## The corpus

Six unrelated source videos. Each was cut to a 5 minute and a 10 minute version,
where **the 5 minute version is exactly the first 5 minutes of the 10 minute
one** (verified by hashing decoded frames at t = 10, 150 and 290 seconds). Each
version then got eight treatments:

| Code | Treatment |
| --- | --- |
| `orig` | Re-encoded only |
| `seg` | Middle 60 per cent kept, ends discarded |
| `small` | Scaled to 1280 wide, frame rate lowered |
| `bars` | 140 px of pure black added top and bottom; the picture itself is untouched |
| `barstext` | Same bars, with advertising text inside them |
| `segadhead` | `seg` with a 70 second advertisement joined to the front |
| `segadtail` | `seg` with the same advertisement joined to the back |
| `combo` | `small` + `barstext` + `seg` + advertisement at the front, all at once |

6 sources x 2 lengths x 8 treatments = 96 files, signatures sampled at 5 fps.
The same 70 second advertisement is used in every file that has one, so files
from different sources genuinely share content without being the same video.
That is deliberate: it is the case that decides where the line goes.

## Ground truth

The filename is the ground truth. `<source>__L<minutes>__<treatment>`:

| Class | Rule | Pairs |
| --- | --- | ---: |
| `same` | Same source, any length, any treatment. Should be found. | **720** |
| `ad` | Different sources, but both files contain the advertisement. Really do share 70 seconds; must **not** be called duplicates. | **540** |
| `unrelated` | Different sources, no shared content. | **3300** |
| | | **4560** |

`same` splits further, and the split matters:

- **336** same-length pairs (L5 x L5 or L10 x L10)
- **384** cross-length pairs (L5 x L10), where one file is a prefix of the other

## Method

Every run used `-i 0 -b 0.1 -k 1` and varied only `-x` and `-m`. thIt and
minScore stay loose on purpose: both are applied after the search rather than
during it (`signature_lookup.c`, the `thit` check is a `continue`, not a
`break`), so one permissive run can be post-filtered on thIt, minScore and
coverage instead of running the sweep once per value.

Two sweeps:

- **Stage 2**: the 48 five-minute files, 1128 pairs, `-x` over 9 values x 2
  modes = 18 runs. 1.28 hours.
- **Stage 4**: all 96 files, 4560 pairs, 15 runs concentrated where stage 2 said
  the answer was. 8.26 hours.

Machine: AMD Ryzen 8845, 14 cores visible under WSL2.

## Mode: `longest` beats `full`

This is the largest single effect measured, and it is not subtle.

`full` stops searching as soon as one walk has reached an end in *each* file.
The status bits are a single mask over the whole walk, not one per file, so
**the head of one file plus the tail of the other satisfies it**. That is
exactly the shape of two videos carrying the same advertisement at opposite
ends: the search locks onto the 70 second advertisement, declares it a whole
match, stops, and never looks at the several minutes the two files really share.

`longest` ranks candidates by match length and does not stop early.

Same-source pairs found at `-x 290`, coverage threshold 40 per cent, out of 720:

| Mode | Found | False positives |
| --- | ---: | ---: |
| `full` | 596 (82.8%) | 0 |
| `longest` | **717 (99.6%)** | 0 |

Broken out by treatment pair, `full` fails precisely where the mechanism
predicts. Each cell is *found / total*:

```
-m full            segadhead  segadtail    combo
segadhead              3/6        6/24     19/24
segadtail                         3/6       6/24
combo                                       3/6
```

`segadhead x segadtail` is 6 of 24. Those pairs share the whole `seg` body,
several minutes of it, and `full` reports the advertisement instead. The
diagonal cells (3 of 6) are the cross-length pairs of one treatment against
itself, which have the same layout.

The same cells under `longest`:

```
-m longest         segadhead  segadtail    combo
segadhead              6/6       24/24     24/24
segadtail                         6/6      24/24
combo                                       6/6
```

`longest` costs nothing at usable thresholds. Per-round wall clock at `-x` 290
and below differed between the modes by up to 17 per cent in either direction,
with no consistent sign, which is machine noise. `longest` is only slower at
`-x` 350 and above, where `full`'s early exit fires on nearly everything — and
those thresholds are unusable for other reasons.

## The threshold trade-off

`-x` (thXh, the per-frame L1 distance threshold) has to be high enough to see
through black bars and low enough that black bars do not create matches. Those
are the same bars.

The bars are 140 px top and bottom, so about **20.6 per cent of every frame is
identical black** in any video carrying them, whatever the video shows. Two
completely unrelated videos that both have bars share that fifth exactly.

The highest coverage reached by any *unrelated* pair:

| `-x` | Highest unrelated | Highest `ad` | Which class sets the ceiling |
| ---: | ---: | ---: | --- |
| 250 | 8.3% | 30.1% | advertisement |
| 270 | 13.2% | 37.1% | advertisement |
| **290** | **20.9%** | **37.1%** | **advertisement** |
| 310 | **50.1%** | 37.5% | **black bars** |
| 330 | 100.0% | 72.5% | black bars |
| 350 | 100.0% | 100.0% | black bars |

At `-x 310` the twelve highest-scoring unrelated pairs all involve bars: ten are
`bars x bars`, one `bars x barstext`, one `bars x combo`.

So the ceiling changes hands between 290 and 310. Up to 290 it is set by pairs
that really do share an advertisement, which is honest and can be filtered on
coverage. From 310 it is set by black bars, where the match is real at the frame
level and means nothing.

An earlier version of this said the bars could not be filtered on anything. That
was wrong: they can be cropped off before the signature is taken, and doing so
is worth more than it appeared here. [Cropping the bars](#cropping-the-bars) has
the measurements. It does not raise the ceiling on `-x`, though. At 350 there
were already 831 unrelated pairs above 20 per cent coverage with no bars on
either side, and cropping leaves that number untouched, so the reason not to go
above 290 survives its own explanation being incomplete.

Recall and false positives at a fixed 40 per cent coverage threshold, `longest`,
out of 720 true and 3840 non-duplicate pairs:

| `-x` | Found | False positives |
| ---: | --- | ---: |
| 200 | 616 (85.6%) | 0 |
| 250 | 689 (95.7%) | 0 |
| 270 | 706 (98.1%) | 0 |
| **290** | **717 (99.6%)** | **0** |
| 310 | 720 (100%) | 3 |
| 330 | 720 (100%) | 159 |
| 350 | 720 (100%) | 1376 |
| 450 | 720 (100%) | 3840 |

`-x 310` is a defensible alternative if a missed duplicate costs more than a
spurious one: it reaches full recall for 3 false positives out of 3840, a rate
of 0.078 per cent. 290 is the default because zero false positives is a cleaner
guarantee, and because the three pairs 290 misses are all one pathological
combination rather than a broad weakness (see below).

## The coverage window

Coverage, not score and not thIt, is what separates the classes. At
`-x 290 -m longest`:

| Class | n | Min | Median | Max |
| --- | ---: | ---: | ---: | ---: |
| `same`, same length | 336 | 30.6% | 84.8% | 100.0% |
| `same`, cross length | 384 | 26.8% | 72.2% | 100.0% |
| `ad` | 540 | 16.3% | 28.1% | **37.1%** |
| `unrelated` | 3300 | 0.9% | 4.2% | 20.9% |

The window for the threshold is **(37.1%, 48.0%]**, about eleven points wide,
bounded on each side by something structural rather than by noise:

- **37.1%** is the most a pure advertisement match reaches. The advertisement is
  350 frames and the shortest file containing one is 1250 frames, so the
  expected value is 28.0 per cent; the walk carries a few frames past each end,
  which accounts for the rest.
- **48.0%** is where **31 genuine duplicates** sit, in a tight cluster. All 31
  are cross-length pairs whose shorter file is a 5 minute variant carrying the
  advertisement, so 1250 frames. The shared content is the overlap between the
  two `seg` windows: the middle 60 per cent of a 5 minute cut is source seconds
  60–240, the middle 60 per cent of the 10 minute cut is 120–480, and they
  overlap for 120 seconds. Measured `matchframes` for the cluster is 600 to 606.
  600 out of 1250 is 48 per cent, and that is the correct answer, not a failure.

Raising the threshold to 50 per cent cuts through that cluster and through the
53 true pairs that sit below 50 per cent in total, dropping recall from 99.6 to
92.6 per cent. Lowering it to 30 per cent admits 25 advertisement-only pairs.

Coverage reads slightly high. Clips using 86, 50 and 30 per cent of a source
came back as 88, 54 and 33, because the walk extends a few frames past what is
really shared. A 40 per cent setting therefore fires at roughly 36 per cent of
real use.

## The weakness that led to cropping

Without the crop, three of 720 true duplicates fall below 40 per cent at
`-x 290 -m longest`. All three are the same source (`20190710`) with the same
treatment pair (`seg` against `combo`), and all three are fixed by cropping:

| Pair | Coverage | Shared content actually recovered |
| --- | ---: | ---: |
| `L10__seg x L5__combo` | 26.8% | 335 of 600 frames (56%) |
| `L10__combo x L10__seg` | 30.6% | 551 of 1800 frames (31%) |
| `L10__combo x L5__seg` | 39.8% | 358 of 600 frames (60%) |

The second one is the clearest statement of the problem: `combo` contains the
entire `seg` body, both files are 1800 frames, and the correct answer is 100 per
cent. Every one of the other five sources returns exactly 100 per cent on that
same pairing.

This was first read as the frame-level walk losing the trail through `combo`'s
five stacked degradations on one particular source's footage. That reading was
wrong. One of those five is black bars, and cropping them off is enough on its
own: the same pair comes back at 1800 of 1800 frames, exactly 100 per cent. The
footage was never the problem. See [Cropping the bars](#cropping-the-bars).

With the bars cropped, no true duplicate in the corpus falls below the 40 per
cent threshold.

## Cropping the bars

Everything above was measured on signatures taken from the videos as they are.
Taking them from the picture instead, with the bars cropped off first, changes
the answer.

`tools/detect_bars.py` finds the bars. It looks for rows that do not move rather
than rows that are dark, because a bar with advertising text in it is not dark
and ffmpeg's `cropdetect` stops at the first line of the text. Over the 96
videos it is right about whether there are bars 96 times, and right about the
exact height 95 times, worst error one row. The crop then rides along in the
same ffmpeg pipeline that takes the fingerprint, so nothing is written to disk
and no second generation of encoding is added; detection costs about a twentieth
of fingerprinting, since it samples 72 frames rather than decoding the file.

All 4560 pairs, `-m longest`, coverage threshold 40 per cent. Separation is the
weakest true duplicate minus the strongest non-duplicate, so a negative number
means the classes overlap and no threshold splits them:

| `-x` | | Weakest duplicate | Strongest other | Separation | Found | False positives |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 250 | as is | 14.8% | 30.1% | −15.2 | 689/720 | 0 |
| 250 | cropped | 23.3% | 28.9% | −5.5 | 700/720 | 0 |
| 270 | as is | 18.2% | 37.1% | −18.9 | 706/720 | 0 |
| 270 | cropped | 31.6% | 30.1% | **+1.4** | 716/720 | 0 |
| **290** | as is | 26.8% | 37.1% | −10.3 | 717/720 | 0 |
| **290** | **cropped** | **48.0%** | **34.1%** | **+13.9** | **720/720** | **0** |
| 310 | as is | 48.0% | 50.1% | −2.1 | 720/720 | 3 |
| 310 | cropped | 48.0% | 37.1% | **+11.0** | 720/720 | 0 |
| 330 | as is | 48.0% | 100.0% | −52.0 | 720/720 | 159 |
| 330 | cropped | 48.0% | 72.5% | −24.5 | 720/720 | 38 |
| 350 | as is | 48.0% | 100.0% | −52.0 | 720/720 | 1376 |
| 350 | cropped | 48.0% | 96.0% | −48.0 | 720/720 | 1090 |

Three things in that table are worth saying out loud.

**The gain is on the duplicates, not the false positives.** At 290 there were no
false positives either way. What cropping does is lift the weakest true
duplicate from 26.8 to 48.0 per cent, and that is what turns an overlap into a
gap. A bar shifts the picture inside the frame, so the same content with and
without one stops lining up, and the signature is comparing different framings.

**48.0 per cent is now the floor, and it is not a failure.** It is
`L5__seg` against a 5 minute file carrying the advertisement: the middle 60 per
cent of a 5 minute cut and of a 10 minute cut of the same source overlap for 120
seconds, which is 600 frames of the 1250 in the shorter file. The comparison
finds all 600. Nothing is being missed there, so this is where the corpus stops
having anything more to give.

**It does not raise the ceiling on `-x`.** 330 and 350 stay unusable. Cropping
removes the bars from the top of the false positive ranking — at 310 the pairs
above 20 per cent coverage with bars on both sides fall from 90 to 15 — but at
350 there were already 831 unrelated pairs above 20 per cent with no bars
anywhere, and that number does not move. Bars were the loudest part of the
problem at 310, not the whole of it at 350.

### What this rests on

The separation at 290 is 13.9 points, bounded below by 48.0 per cent and above
by 34.1 per cent. Both numbers come from the same denominator, the length of a
file carrying the advertisement, which is 900 frames of body plus A frames of
advertisement:

```
strongest advertisement-only pair   =  A  / (900 + A)
weakest true duplicate              = 600 / (900 + A)
```

The advertisement here is 70 seconds, so 350 frames, and the two work out at 28
and 48 per cent. They meet at A = 600, an advertisement of **two minutes**, and
past that they are the wrong way round.

So coverage separates these classes because this corpus's advertisement is short
relative to the body it is attached to, not because the method distinguishes
sharing-because-same-video from sharing-because-same-insert. It cannot: it
measures how much is shared, not what. A corpus built with longer inserts would
need something else, most likely where the shared region sits in each file
rather than how big it is.

The measured advertisement-only ceiling is 34.1 per cent against the 28 the
arithmetic predicts. The difference is the walk running past the end of what is
really shared: median `matchframes` for those pairs is 353 against an
advertisement of 350, but the worst is 427. Six of the 13.9 points are being
spent on that overshoot.

## Scale invariance

The 48-file and 96-file sweeps agree closely on the shape of the curve, which
suggests the thresholds are properties of the treatments rather than of this
corpus size. Fraction of all pairs reported at each `-x`:

| `-x` | 1128 pairs | 4560 pairs |
| ---: | ---: | ---: |
| 60 | 14.0% | 14.9% |
| 90 | 21.5% | 22.3% |
| 116 | 26.9% | 27.6% |
| 150 | 31.7% | 32.6% |
| 200 | 77.7% | 80.1% |
| 250 and up | 100% | 100% |

Note that from `-x 250` upward every pair produces some match. `-x` alone is
therefore not a filter at those values; the filtering is entirely coverage's
job.

## Reproducing

The corpus is built from your own source videos; nothing here depends on the
specific six used. `bench/make-variants.sh` produces the 96 files,
`bench/make-bench-sigs.sh` the signatures, `bench/run-plan.sh` the sweep and
`bench/analyse.py` the report.

```sh
./run-plan.sh 1    # control: do -d and -c do anything (24 signatures, 3 min)
./run-plan.sh 2    # sweep, 48 five-minute signatures, 18 runs, 1.3 h
./run-plan.sh 4    # sweep, all 96, 15 runs concentrated at the answer, 10 h
```

Runs are keyed by label, so an interrupted sweep resumes by rerunning the same
command. Each run records the `mpeg7dupes` version that produced it, read back
from the log rather than assumed, so a result and its build cannot drift apart.

## What this does not tell you

Stated plainly, so nothing here is read as more than it is.

- **One corpus, six sources.** The treatments are synthetic and applied
  uniformly. Real re-uploads vary more.
- **One sampling rate.** Everything is at 5 fps. Whether 2 or 3 fps would hold
  up is untested.
- **`-d` and `-c` were checked and left alone.** Their ratio is an integer
  division of two popcounts, so it is only ever 0 or 1 and never reaches the
  defaults of 9000 and 60000. Forcing both to 1 changes which Hough peak is
  selected — score, offset and seed frame all move — but the matched region,
  frame counts and boundaries come back identical. It is 27 per cent faster and
  rejects the most similar coarse signatures, so it is not a safe optimisation.
- **`-m fast` is not in the sweep.** It takes the first candidate that
  qualifies where `full` at least chooses between them, and in every pilot it
  equalled `full` or was worse.
- **`matchframes` counts the seed frame twice.** A match spanning a whole file
  reports one frame more than the file holds, so coverage can read 100.07 per
  cent. Verified as exactly +1 in every affected row across all runs. Harmless
  to any fractional threshold; do not mistake it for a real overshoot. Tracked
  in `todo.md`.
- **Coverage thresholds swept in post-processing are a lower bound.** A
  candidate selected under loose settings is not necessarily the one that would
  be selected under strict settings, so a real run at the chosen values can do
  better than the table says, not worse.
