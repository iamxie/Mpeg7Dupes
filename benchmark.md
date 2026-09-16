# Benchmark

The detector measurements below describe version 2 where indicated. Build 11
uses detector version 3, with the same motion thresholds and corrected video
stream selection, display height after rotation, and sampling-error handling.
Its input contracts are covered by synthetic smoke tests; these historical
measurements have not been relabelled as a new independent validation.
The [third set](#third-independent-validation-2026-09-16) independently measures
build 12 with motion 3 and black-1 on newly acquired videos.
Those historical results remain frozen. The same videos subsequently informed
the optional fixed-crop feature below, so its results are development evidence.

What settings to use, the measurements behind them, and what those
measurements do and do not tell you.

## The answer

Crop any bars off the top and bottom first, then run with the defaults,
which since build 4 are the settings measured here:

```sh
mpeg7dupes -l list.txt
```

and keep pairs whose **coverage** is at least 40 per cent, where

```
coverage = matchframes / min(frames in file A, frames in file B)
```

`tools/find_reuse.py` does all of it, cropping included, for the question
"does their video contain my clip"; for that question it divides by the
source's own frame count instead, which is not the measure below.

Each setting, and why:

| Flag | Value | Reason |
| --- | --- | --- |
| `-m` | `longest` | `full` stops the search too early on a common real-world layout and loses 17 per cent of true duplicates. See [Mode](#mode-longest-beats-full). Both `full` and `fast` were removed in build 10 |
| `-x` | `290` | The last threshold before black bars start matching unrelated videos to each other. See [The threshold trade-off](#the-threshold-trade-off) |
| `-i` | `0` | Every walk is reported in full. Build 4 made `-i` a filter on the finished walk; up to build 3 any other value truncated the match, which is why every run here used 0 |
| `-b` | `0.5` | thIt. The sweep ran with `-b 0.1`, and on build 2 that value was truncated to 0 on the way in, so nothing was filtered during the search. A reported candidate never has fewer than about one good frame in six, and build 5 at the default 0.5 gives the same rows as 0.1 on the regression subset ([Build 5](#build-5-on-the-same-signatures)) |
| `-k` | `1` | minScore. The score counts votes for the alignment, not frames; on 300 corpus pairs the old default of 49 hid 11 of 60 genuine duplicates, each over 900 frames long. Coverage does the filtering afterwards, and does it better |
| coverage | `>= 0.40` | Sits in the empty band between pairs that share only an advertisement (34.1 per cent at most, cropped) and genuine duplicates (48.0 per cent at least, for the tightest legitimate case). On the second validation set, a pair that is not a duplicate came to 37.8: five minute cuts of two episodes of one series, each starting with the same title sequence and given the same advertisement in front |
| crop | before all of it | Bars are a fifth of the frame and identical in every video carrying them, and they shift the picture inside the frame so a copy with bars and one without stop lining up. `tools/detect_bars.py` finds them |

## Where the numbers come from, and their standing

One corpus of 96 videos built from six sources, compared as all 4560 pairs,
swept with `mpeg7dupes v0.1 b2` on an AMD Ryzen 8845 under WSL2, 14 cores.
With the bars cropped, the settings above find **720 of 720 true duplicates
with zero false positives** on it, and 717 of 720 with the bars left on.

Two things about that figure have to be said before anything else.

**It is a fit, not a forecast.** The mode, the threshold, the coverage rule
and the cropping were all chosen by looking at this corpus. It is a
regression set from here on, and the same material cannot also serve as the
independent validation. Two validation sets were run separately, each once,
on six and then ten other sources, split by source and never by treatment,
and are reported apart in [Independent validation](#independent-validation)
and [Second independent validation](#second-independent-validation). They
say the rule for "are these the same video" was not merely fitted to these
six sources, and the second found where the rule for "does their video
contain my clip" breaks: sources under about two minutes whose picture has a
common light layout. No set gives a base rate for yours.

**It is bound to a build and to a set of signatures.** Every run below is
recorded with the build that produced it, read back from the run's own log.
Build 5 was rerun on the same signatures and gives the same matched frames
pair for pair; the section [Build 5 on the same signatures](#build-5-on-the-same-signatures)
has the identities. The signatures were generated on 2026-09-06 with the
ffmpeg then on the machine, whose version was not written down; the cropped
set on 2026-09-07 with the detector of that day. Both sets are identified by
content digest in the run records, so a result can always be traced to the
exact files, if not to the exact ffmpeg.

## The corpus

Six unrelated source videos. Each was cut to a 5 minute and a 10 minute
version, where **the 5 minute version is exactly the first 5 minutes of the
10 minute one** (verified by hashing decoded frames at t = 10, 150 and 290
seconds). Each version then got eight treatments, applied with ffmpeg:

| Code | Treatment | Recipe |
| --- | --- | --- |
| `orig` | Re-encoded only | `-c copy` of the cut |
| `seg` | Middle 60 per cent kept, ends discarded | `-ss 0.2·D -t 0.6·D` |
| `small` | Scaled to 1280 wide, frame rate lowered | `scale=1280:-2,fps=N`, N being 30 for 50+ fps sources, 24 for 28–50, unchanged below |
| `bars` | 140 px of pure black added top and bottom; the picture itself is untouched | `pad=W:H+280:0:140:black` |
| `barstext` | Same bars, with advertising text inside them | the pad, then two overlays |
| `segadhead` | `seg` with a 70 second advertisement joined to the front | `concat` of the advertisement and the cut, both scaled to the source's size and rate |
| `segadtail` | `seg` with the same advertisement joined to the back | the same, other order |
| `combo` | `small` + `barstext` + `seg` + advertisement at the front, all at once | all of the above, bars 94 px at 1280 wide |

6 sources × 2 lengths × 8 treatments = 96 files, signatures sampled at 5 fps.
The same 70 second advertisement is used in every file that has one, so files
from different sources genuinely share content without being the same video.
That is deliberate: it is the case that decides where the line goes.

The source videos are private and are not in this repository, and neither
are the scripts that cut, treat and fingerprint them; those live in the
experiment area beside it. The recipes above, the ground truth rule below
and the run records are what is needed to build the same corpus from your
own sources.

## Ground truth

The filename is the ground truth. `<source>__L<minutes>__<treatment>`:

| Class | Rule | Pairs |
| --- | --- | ---: |
| `same` | Same source, any length, any treatment. Should be found. | **720** |
| `ad` | Different sources, but both files contain the advertisement. Really do share 70 seconds; must **not** be called duplicates. | **540** |
| `unrelated` | Different sources, no shared content. | **3300** |
| | | **4560** |

`same` splits further, and the split matters:

- **336** same-length pairs (L5 × L5 or L10 × L10)
- **384** cross-length pairs (L5 × L10), where one file is a prefix of the other

## Method

Every run used `-i 0 -b 0.1 -k 1` and varied only `-x` and `-m`. thIt and
minScore stay loose on purpose: both are applied after the search rather than
during it (`signature_lookup.c`, the `thit` check is a `continue`, not a
`break`), so one permissive run can be post-filtered on thIt, minScore and
coverage instead of running the sweep once per value.

Two sweeps:

- **Stage 2**: the 48 five-minute files, 1128 pairs, `-x` over 9 values × 2
  modes = 18 runs. 1.28 hours.
- **Stage 4**: all 96 files, 4560 pairs, 15 runs concentrated where stage 2
  said the answer was. 8.26 hours.

Then the cropped set, all 96 files at six thresholds in `longest`.

## Mode: `longest` beats `full`

Kept as history: build 10 removed `full` and `fast`, on this measurement and
on the fixtures, where `full` settled on a ten-frame candidate at an extreme
ratio once build 7 proposed more candidates. `longest` is the only mode.
This is the largest single effect measured, and it is not subtle.

`full` stops searching as soon as one walk has reached an end in *each* file.
The status bits are a single mask over the whole walk, not one per file, so
**the head of one file plus the tail of the other satisfies it**. That is
exactly the shape of two videos carrying the same advertisement at opposite
ends: the search locks onto the 70 second advertisement, declares it a whole
match, stops, and never looks at the several minutes the two files really
share.

`longest` ranks candidates by match length and does not stop early.

Same-source pairs found at `-x 290`, coverage threshold 40 per cent, out of
720, bars left on:

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

`segadhead × segadtail` is 6 of 24. Those pairs share the whole `seg` body,
several minutes of it, and `full` reports the advertisement instead. The
diagonal cells (3 of 6) are the cross-length pairs of one treatment against
itself, which have the same layout. Under `longest` every one of those cells
is full.

`longest` costs nothing at usable thresholds. Per-round wall clock at `-x`
290 and below differed between the modes by up to 17 per cent in either
direction, with no consistent sign, which is machine noise. `longest` is only
slower at `-x` 350 and above, where `full`'s early exit fires on nearly
everything, and those thresholds are unusable for other reasons.

## The threshold trade-off

`-x` (thXh, the per-frame L1 distance threshold) has to be high enough to see
through black bars and low enough that black bars do not create matches.
Those are the same bars.

The bars are 140 px top and bottom, so about **20.6 per cent of every frame
is identical black** in any video carrying them, whatever the video shows.
Two completely unrelated videos that both have bars share that fifth exactly.

The highest coverage reached by any *unrelated* pair, bars on:

| `-x` | Highest unrelated | Highest `ad` | Which class sets the ceiling |
| ---: | ---: | ---: | --- |
| 250 | 8.3% | 30.1% | advertisement |
| 270 | 13.2% | 37.1% | advertisement |
| **290** | **20.9%** | **37.1%** | **advertisement** |
| 310 | **50.1%** | 37.5% | **black bars** |
| 330 | 100.0% | 72.5% | black bars |
| 350 | 100.0% | 100.0% | black bars |

At `-x 310` the twelve highest-scoring unrelated pairs all involve bars. So
the ceiling changes hands between 290 and 310. Up to 290 it is set by pairs
that really do share an advertisement, which is honest and can be filtered on
coverage. From 310 it is set by black bars, where the match is real at the
frame level and means nothing. Cropping the bars off does not raise that
ceiling: at 350 there were already 831 unrelated pairs above 20 per cent
coverage with no bars on either side, and cropping leaves that number
untouched.

Recall and false positives at a fixed 40 per cent coverage threshold,
`longest`, bars on, out of 720 true and 3840 non-duplicate pairs:

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
spurious one: full recall for 3 false positives in 3840. 290 is the default
because zero false positives is a cleaner guarantee, and because the three
pairs 290 misses are one pathological combination that cropping fixes.

## The coverage window

Coverage, not score and not thIt, is what separates the classes. At
`-x 290 -m longest`, bars on:

| Class | n | Min | Median | Max |
| --- | ---: | ---: | ---: | ---: |
| `same`, same length | 336 | 30.6% | 84.8% | 100.0% |
| `same`, cross length | 384 | 26.8% | 72.2% | 100.0% |
| `ad` | 540 | 16.3% | 28.1% | **37.1%** |
| `unrelated` | 3300 | 0.9% | 4.2% | 20.9% |

The window for the threshold is **(37.1%, 48.0%]**, about eleven points
wide, bounded on each side by something structural rather than by noise:

- **37.1%** is the most a pure advertisement match reaches. The
  advertisement is 350 frames and the shortest file containing one is 1250
  frames, so the expected value is 28.0 per cent; the walk carries a few
  frames past each end, which accounts for the rest.
- **48.0%** is where **31 genuine duplicates** sit, in a tight cluster. All
  31 are cross-length pairs whose shorter file is a 5 minute variant
  carrying the advertisement, so 1250 frames. The shared content is the
  overlap between the two `seg` windows: 120 seconds, 600 frames, and 600 of
  1250 is 48 per cent. That is the correct answer, not a failure.

Raising the threshold to 50 per cent cuts through that cluster, dropping
recall from 99.6 to 92.6 per cent. Lowering it to 30 per cent admits 25
advertisement-only pairs.

Coverage reads slightly high. Clips using 86, 50 and 30 per cent of a source
came back as 88, 54 and 33, because the walk extends a few frames past what
is really shared. A 40 per cent setting therefore fires at roughly 36 per
cent of real use.

## The weakness that led to cropping

Without the crop, three of 720 true duplicates fall below 40 per cent at
`-x 290 -m longest`. All three are the same source (`20190710`) with the
same treatment pair (`seg` against `combo`):

| Pair | Coverage | Shared content actually recovered |
| --- | ---: | ---: |
| `L10__seg × L5__combo` | 26.8% | 335 of 600 frames (56%) |
| `L10__combo × L10__seg` | 30.6% | 551 of 1800 frames (31%) |
| `L10__combo × L5__seg` | 39.8% | 358 of 600 frames (60%) |

The second is the clearest statement of the problem: `combo` contains the
entire `seg` body, both files are 1800 frames, and the correct answer is 100
per cent. Every one of the other five sources returns exactly 100 per cent on
that pairing. This was first read as the walk losing the trail through
`combo`'s five stacked degradations on one source's footage. That reading was
wrong: one of the five is black bars, and cropping them off is enough on its
own. The same pair comes back at 1800 of 1800 frames.

## Cropping the bars

`tools/detect_bars.py` finds the bars. It looks for rows that do not move
rather than rows that are dark, because a bar with advertising text in it is
not dark and ffmpeg's `cropdetect` stops at the first line of the text. Over
the 96 videos it is right about whether there are bars 96 times, and right
about the exact height 95 times, worst error one row. Version 2 of the
detector, which came out of the independent validation below, answers the
same on all 96. The crop rides along
in the same ffmpeg pipeline that takes the fingerprint; detection costs about
a twentieth of fingerprinting.

All 4560 pairs, `-m longest`, coverage threshold 40 per cent. Separation is
the weakest true duplicate minus the strongest non-duplicate, so a negative
number means the classes overlap and no threshold splits them:

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

**The gain is on the duplicates, not the false positives.** At 290 there
were no false positives either way. What cropping does is lift the weakest
true duplicate from 26.8 to 48.0 per cent, and that is what turns an overlap
into a gap. **48.0 per cent is now the floor, and it is not a failure**: it
is the 120 second overlap of two `seg` windows, found in full. **It does not
raise the ceiling on `-x`**: 330 and 350 stay unusable.

### What this rests on

The separation at 290 is 13.9 points, bounded below by 48.0 per cent and
above by 34.1 per cent. Both come from the same denominator, the length of a
file carrying the advertisement, 900 frames of body plus A frames of
advertisement:

```
strongest advertisement-only pair   =  A  / (900 + A)
weakest true duplicate              = 600 / (900 + A)
```

The advertisement here is 70 seconds, 350 frames, and the two work out at 28
and 48 per cent. They meet at A = 600, an advertisement of **two minutes**,
and past that they are the wrong way round.

So coverage separates these classes because this corpus's advertisement is
short relative to the body it is attached to, not because the method
distinguishes sharing-because-same-video from sharing-because-same-insert.
It cannot: it measures how much is shared, not what. The synthetic check
below makes the same point with an 8 second opening on 20 second clips. A
corpus built with longer inserts would need something else, most likely where
the shared region sits in each file rather than how big it is; the boundary
columns carry that.

The measured advertisement-only ceiling is 34.1 per cent against the 28 the
arithmetic predicts. The difference is the walk running past the end of what
is really shared: median `matchframes` for those pairs is 353 against an
advertisement of 350, but the worst is 427.

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

From `-x 250` upward every pair produces some match. `-x` alone is therefore
not a filter at those values; the filtering is entirely coverage's job.

## Build 5 on the same signatures

Builds 3 to 5 changed the comparator's contract: `-b` reaches the core as a
ratio where it used to be truncated to an integer, `-i` filters instead of
truncating, the defaults changed, and the loader validates its input. None of
that should move a result taken with `-i 0` and an effectively unfiltered
`-b`, and this checks that it did not. Run on 2026-09-10 inside the test
container, debian bookworm on aarch64, 8 cores, with the signatures from the
b2 sweeps, identified by digest in each run's ledger-style record below.

| Run | Signatures | Pairs | Settings | Result against build 2 |
| --- | --- | ---: | --- | --- |
| L5, bars on | `sig/fps5`, 48 files | 1128 | `-i 0 -b 0.1 -k 1 -x 290 -m longest` | `matchframes` identical on all 1128 pairs; 1117 rows byte-identical, the other 12 the same pair with the files in the other order (the list was sorted differently), which changes `score` and `offset` only. At 40 per cent: 168 of 168 `same` found, 0 of 135 `ad` and 0 of 825 `unrelated` reported, as on build 2 |
| L5, cropped | `sig/fps5-crop`, 48 files | 1128 | the same | `matchframes` identical on all 1128 pairs against the L5 rows of the build 2 cropped run; 1116 rows byte-identical, the other 12 the orientation swap above. At 40 per cent: 168 of 168 `same`, 0 of 135 `ad`, 0 of 825 `unrelated` |
| L5, cropped, defaults | `sig/fps5-crop`, 48 files | 1128 | `-x 290` only, so `-b 0.5` | 1127 of 1128 rows identical to the `-b 0.1` run in matched, good and total frames, every `same` row among them. The one that differs is an `unrelated` pair whose loose candidate had fewer good frames than bad; at 0.5 that candidate is rejected and a shorter one reported. So the default filters nothing that matters and the sweep's numbers describe the default as well |
| All 96, cropped | `sig/fps5-crop`, 96 files | 4560 | `-i 0 -b 0.1 -k 1 -m longest -x 290` | `matchframes` identical on all 4560 pairs against the build 2 cropped run; 4536 rows byte-identical, the other 24 the same pair with the files in the other order. At 40 per cent: 720 of 720 `same`, 0 of 540 `ad`, 0 of 3300 `unrelated`; the weakest duplicate at 48.0 per cent, the strongest advertisement pair at 34.1 and the strongest unrelated pair at 20.3, which is the window measured on build 2. 2397 s on 8 cores |

Identity of the build: `mpeg7dupes v0.1 b5`, sha256 of the binary
`d8790bd8ba9c9abefd6bbe1c91380ce8a8d391f66df003435c785210dbdf47ba`, built
in the container with `make release`. Build 6 followed it with a change to
the argument check only; the comparison code is the same.

## Build 7: the comparison changed, and what it did to the corpus

Build 7 fixed four things in the comparison, each with a unit test that
fails on build 6 (`tests/README.md`): the coarse filter, which never
rejected a pair of segments, now rejects one when three of its five words
share a tenth or less; the candidate scan covers the whole Hough
accumulator, where it used to stop at the middle and never propose an
alignment with a positive offset; the walk steps the two clips at the
voted ratio, where it used to truncate the step and lose any copy at
another speed; and among candidates of the same length `longest` takes the
closer one rather than the first evaluated. Run on the 48 cropped
five-minute signatures, 1128 pairs, `-i 0 -b 0.1 -k 1 -m longest -x 290`,
in the test container on 8 cores:

| Comparison | Result |
| --- | --- |
| Filter on against filter off (`-d 10001`), build 7 | All 168 `same` pairs identical in matched frames, good and total frames, mean distance and both spans. 571 `other` pairs differ, in the handful of noise frames they report. At 40 per cent: 168 of 168, 0 of 135 `ad`, 0 of 825 `unrelated` either way. 101 s against 280 s: the filter takes about two thirds off the comparison time |
| Build 7 against build 5, same signatures | 153 of 168 `same` pairs identical. 10 come back the same length with one boundary a frame away and a lower mean distance, the tie rule choosing the closer of two equally long walks (for instance 84.0 against 100.3, with 916 good frames against 877). 5 come back one frame longer and noisier, a walk a frame out of step that happened to run one frame further, which `longest` prefers by length; their boundaries agree to within a frame. The coverage window is unchanged: weakest `same` 72.0 per cent, strongest `ad` 32.2, strongest `unrelated` 15.2 against 14.9 on build 5 |
| All 96 cropped signatures, 4560 pairs, build 7 against build 5 | 670 of 720 `same` pairs identical; 25 the same length, every one of them closer, and 25 one frame longer, the same two effects as above, with no boundary moving by more than two frames, and all but two by one. At 40 per cent: 720 of 720, 0 of 540, 0 of 3300; weakest `same` 48.0 per cent, strongest `ad` 34.1, strongest `unrelated` 17.3 against 20.3 on build 5. 891 s, where build 5 took 2397 s on the same machine in a cooler state, so read the ratio, not the seconds |

The cost of the comparison, measured on 24 of those signatures, 276
pairs, with the release binary instrumented around each stage and run
back to back, 8 cores:

| Run | Wall | Thread-seconds: load first, load second, compare, print |
| --- | ---: | --- |
| build 7, filter on | 51 s | 0.06, 0.33, 384.7, 0.01 |
| build 7, filter off | 179 s | 0.09, 0.52, 1406.9, 0.17 |
| build 5 | 169 s | not instrumented |
| build 7, filter on, `-j 1` | 197 s | 0.02, 0.24, 196.8, 0.02 |

Compare is 99.9 per cent of the time whatever the filter does; loading the
second signature of each pair, which the todo lists as the next thing to
cache, is 1.2 ms per pair, a thousandth of it. Peak memory was 10 MB
throughout. `fill_l1distlut`, run once per pair, takes 335 µs, a
twentieth of a per cent. None of that is worth a change; the filter was,
and it took 72 per cent off the compare time here and 64 per cent off the
1128-pair round. Absolute times on this machine drift with its
temperature, which is why every comparison above was run back to back.

Determinism, on the same 24 signatures: `-j 1` and eight cores give 276
rows identical to the byte. With the list reversed, 273 rows agree in
matched frames, good and total frames, mean distance and both spans, and
the other three, all pairs of unrelated files reporting twenty-odd noise
frames, settle on a different piece of noise. The earlier note that two
machines disagreed on three of 4560 pairs was not reproduced; build 5 on
aarch64 and build 2 on x86_64 agreed on all of them in milestone F.

Identity of the build: `mpeg7dupes v0.1 b7`, sha256 of the binary
`faecdaa018dd69d32d7b45de35830b537312233b073e043c8f32d12705424f6f`, built in
the container with `make release`.

## Scenarios the corpus does not cover, on synthetic clips

The eight treatments cover re-encoding, a change of resolution, trimming,
bars and a long shared insert. Five scenarios the corpus does not cover were
checked on synthetic clips with ffmpeg 5.1, on build 5 and again on build 7: every clip is a
lavfi source at 320x180 and 25 fps, signatures at 5 fps, the defaults
throughout. Synthetic material shows the shape of the behaviour and nothing
about rates on real footage.

| Scenario | Construction | Result |
| --- | --- | --- |
| Shared opening, long bodies | the same 8 s `testsrc2` opening in front of 52 s of `mandelbrot` and 52 s of `life` | 43 frames matched at 0.00–7.80 in both, 14.3 per cent of either: below the rule, not a duplicate. Correct |
| Shared opening, short bodies | the same opening in front of 12 s bodies | the same 43 frames are 43.0 per cent of either: **above the rule, reported as a duplicate**. The structural limit from [What this rests on](#what-this-rests-on), reproduced |
| Short quotation | 12 s of a 60 s `mandelbrot` source placed at 25 s inside 60 s of `life` | found, 66 frames, at 25.00–36.80 of the quoting file, which is right. On build 5 the speed ratio was voted at 0.07 and the source-side span came back as 11.40–35.00 where 20.00–31.80 is right, because the candidate scan covered only half of the accumulator; on build 7 the ratio is 1.0 and the span 20.00–31.80. 22 per cent of the source, so under the default threshold of `find_reuse.py`; at `--min-coverage 15` it is reported |
| Another speed | the 60 s source played at 1.25x (48 s, 240 frames) and at 0.8x (75 s, 375 frames) | build 5, from an earlier measurement of the same construction: 117 of 240 frames for the fast copy, under the threshold. Build 7: 240 of 240 and 300 of 300, both whole, ratios 0.80 and 1.20 on the grid of thirtieths; the 0.8x copy is placed 2.6 s in where 0 is right, the grid's 1.20 against a true 1.25 drifting 15 frames over 300, and `find_reuse.py` reads 80 per cent for the fast copy because `matchframes` counts the frames of the slower clip |
| Static, featureless | 40 s of solid gray, of solid blue, of `smptebars`, of `testsrc`, of `testsrc2`, and the gray clip scaled to 240x136 | no row at all for any pair of different clips, and none for the gray clip against its own re-encode. Colour bars against a copy of themselves with 30 px black bars added: 200 of 200 frames. No false positive was produced; a featureless genuine duplicate is missed outright |
| Bars the detector cannot read | the `smptebars` clip and the `mandelbrot` body, each with 30 px black bars added top and bottom, through `find_reuse.py` | the moving body's bars were detected as `crop=iw:180:0:30`; both static clips came back `uncertain` and were fingerprinted uncropped. The barred static clip still matched its unbarred original end to end, since 30 px on 240 is under what `-x 290` tolerates |

Absent from every synthetic check, then, is any false positive from static
footage; what is present is a silent miss on featureless footage, the
opening-length limit, and at another speed the grid's coarseness. Real
low-motion footage was measured afterwards, in [Independent
validation](#independent-validation): a sunset and a night sky were
recognised in their own copies, their positions were not reliable, and the
bar detector showed a fault these synthetic clips could not.

## Independent validation

Run once, on 2026-09-11, after every setting above was fixed, on material
that had no part in choosing them. Nothing was tuned on it. The one decision
it was meant to inform, the coarse filter's default, is set out below; the
default was kept.

### The material

Six videos from sources the tuning corpus does not use, one of each kind it
lacked: a talk show at a dinner table, an animation, a basketball
compilation, a star field flying past, a sunset from a locked-off camera, and
a night sky over a ridge, the last two barely moving. Each was cut to five
minutes and given the eight treatments of [The corpus](#the-corpus), and four
more:

| Code | Treatment |
| --- | --- |
| `intro` | The same 8 s opening in front of the whole cut |
| `quote` | 20 s of the next source spliced in at 100 s |
| `quoteshort` | 10 s of the source after that spliced in at 220 s |
| `speed` | Played at 1.25x, frame rate kept |

What differs from the tuning recipes: `orig` and `seg` are re-encoded rather
than stream-copied, so that every cut point is exact to the frame, which
PSNR at the joins confirms (44 to 57 dB aligned against 22 to 32 dB a frame
off; a splice can land one frame late, a fifth of a frame at 5 fps). The
advertisement is a new one, 60 s, generated with ffmpeg rather than cut from
any video, so that it resembles none of the sources; a generated insert
matches cleanly, which makes it the hard case for the 40 per cent rule, not
the easy one. The 8 s opening is generated the same way, and the text in the
bars is new.

Two of the sources turned out to be short loops played for many minutes: the
star field repeats every 40 s and the night sky every 4.64 s, frames matching
at 48 to 49 dB PSNR at the period and at 25 to 32 dB a frame away from it.
Positions on them are scored modulo the period, since those frames really are
the same. The sunset does not loop, 26.5 dB between frames 73 s apart, but
any minute of it matches any other.

That makes 72 files and 2556 pairs for "are these the same video". For "does
their video contain my clip" the sources are the six cuts, a 20 s and a 10 s
clip from each, the advertisement and the opening, 20 in all, against the 72.
The ground truth is by construction: a manifest records, for every file,
which stretch of which material sits where and at what speed, and what two
files share follows from it. A candidate holding at least 40 per cent of a
source's material should be reported as containing it; no construction lands
between 20 and 50 per cent.

Build `mpeg7dupes v0.1 b9`, whose comparison code is build 7's, sha256
`242a1372181eba1cf314f602fcf7b539c21a07d7d9e6d6e96e69e0ab432a04bf`, run in
the test container on 8 cores, at the defaults throughout: `mpeg7dupes -l`
for the first question and `tools/find_reuse.py` for the second. Signatures
at 5 fps with the bars cropped, detector version 1, made with ffmpeg 9.0.1
on macOS: 657 s for the 92 files. The run records, the signature digests and
the result for every pair are kept beside the material, outside this
repository.

### Are these the same video

| Class | Pairs | Reported | Constructed coverage | Highest measured |
| --- | ---: | ---: | ---: | ---: |
| same source | 396 | **382** | 38.7 to 100% | 100% |
| share the advertisement | 135 | 0 | 25.0% | 30.2% |
| share the opening | 15 | 0 | 2.6% | 3.6% |
| share a quoted clip | 146 | 0 | 3.2 to 11.1% | 16.0% |
| unrelated | 1864 | 0 | 0 | 13.0% |

No false positive in 2160 pairs, and the most a non-duplicate reached, 30.2
per cent, is a pair sharing the advertisement, as on the tuning corpus. Of
the 14 misses:

- **Three are structural.** `quote` and `quoteshort` of one source each have
  a clip spliced in at a different place, which cuts the body they share
  into three pieces, the longest 120 s, 38.7 per cent of the shorter file.
  The comparison reports one match per pair, so no accuracy gets them over
  40; the fourth such pair came out at 40.4 per cent through the walk's
  usual overrun.
- **Eleven are the sunset**, and every one of them pairs a file with a copy
  the bar detector cropped although it has no bars ([The bar
  detector](#the-bar-detector)). With the coarse filter off all eleven are
  found.

Where the 382 were placed: the start lands within a frame on both files in
323 pairs and within five frames in 350, and the end within a frame in 349.
Nearly all the rest are the sunset, where the walk locks onto whichever
stretch of the shot looks alike and so reports a true duplicate at the wrong
place, up to 60 s off; and two night-sky pairs at another speed, where a
4.64 s loop lets the vote settle on the wrong ratio.

### Does their video contain my clip

| Source | Found | Reported wrongly | Start in their video, median / worst, frames |
| --- | ---: | ---: | ---: |
| 5 minute cut | 71 of 72 | 0 | 0 / 25 |
| 20 s clip | 79 of 80 | 2 | 0 / 18 |
| 10 s clip | 75 of 80 | 1 | 0 / 9 |
| 60 s advertisement | 18 of 18 | 0 | 0 / 60 |
| 8 s opening | 6 of 6 | 0 | 0 / 0 |

The run was complete: all 20 sources and 72 candidates processed, none
failed. The quotations are what the tuning corpus could not test. Every 20 s
quote was found where it had been spliced in, the two taken from the still
sources included, and the 10 s ones in four places of six. The 8 s opening,
under the 10 s the synthetic checks put as the floor, was found in all six
files and placed exactly. Every 1.25x copy of a cut was found and placed
exactly, the ratio voted at 0.80, which is on the grid; coverage read 80
per cent for all six, as [todo.md](todo.md) says it will.

The misses are all the sunset's: five involve the copies cropped wrongly, one
is its 10 s clip spliced into the basketball compilation, and one that clip
inside its own barred copy, which the detector left uncropped. Of the three
reported wrongly, one is the sunset's 10 s clip found at 100 per cent in
another stretch of the same shot 50 s away, a different moment of the same
picture; one is a 20 s basketball clip reported at exactly 40.0 per cent
against another clip from the same compilation, at a speed ratio of 0.47;
and one is against a sunset copy cropped wrongly.

### The coarse filter

todo.md left one decision to this set: `-d 9000` stays unless a same-source
pair is missed with the filter on and found with `-d 10001`. The same run
with only `-d 10001` added, for both questions:

| | Filter on, `-d 9000` | Filter off, `-d 10001` |
| --- | ---: | ---: |
| Same video: true pairs found, of 396 | 382 | 394 |
| Same video: other pairs reported, of 2160 | 0 | 0 |
| Contains my clip: true matches found, of 256 | 249 | 256 |
| Contains my clip: other pairs reported, of 1184 | 3 | 207 |
| Comparison time, same video / contains my clip | 190 s / 204 s | 595 s / 597 s |

Nothing is found with the filter on and lost with it off. What it loses is
the sunset: 12 same-source pairs, the ten against the cropped `segadtail`
rising from between 0 and 18 per cent to between 67 and 100, and 7 clip
matches. So the condition is met. What the condition did not ask about is
the other column: with the filter off, clips of 10 and 20 s from every source
and the 8 s opening turn up in unrelated videos at 40 to 100 per cent, 207
times, most often the dark and still ones. For the first question the 40 per
cent rule over five minute files absorbs those spurious runs; for the second,
where the source is a short clip, nothing does. The filter is a guard for
short sources as well as a saving. On 2026-09-11 the owner kept the default
at 9000, which changes no setting and leaves this set a validation set.

### The bar detector

On the four sources that move it found every bar: `bars` and `barstext` at
140 rows and `combo` at 94, within a row, and `combo` of the two still
sources too, whose advertisement moves. On the files of the two still sources
that are still throughout it answered as designed, too static to tell, and
they were fingerprinted uncropped. Those barred copies were still found as
duplicates of every sibling not cropped wrongly, the bars matching through at
`-x 290` as they did on the tuning corpus. That is the case of bars the
detector cannot decide about, and it held.

What went wrong is new. Three files without bars were cropped as if they had
them: the sunset with the talk show's 10 s clip spliced in lost 482 of its
1080 rows, the sunset with the advertisement at its tail lost the top 274,
and the night sky with an animation clip lost 136. Each is a still shot with
a short moving stretch, and in each one of the detector's six sampling
windows falls in that stretch: the middle of the frame moves there, which
lifts the guard for still footage, and the still rows at the edges then read
as bars. The two cropped sunset copies are behind 17 of the 24 errors above.

Version 2 of the detector also asks that a bar look the same in every
sampling window, which leaves those rows alone: the three files come back
with no bars, and nothing else changes by a row, on this set or on the 96
of the tuning corpus. Run again with it and the filter on, the set gives 394
of 396 same-video pairs, the two left being the structural ones, and 254 of
256 contains-my-clip matches with 2 false ones. The misses left are the
sunset's 10 s clip, inside the basketball compilation and inside its own
barred copy; the false ones are the two above that involved no cropped copy.
So every same-video pair the coarse filter seemed to lose was lost to the
cropping, and what the filter still costs here is those two clip matches.
These numbers come from a detector changed after looking at this set, so
they are not independent: the set is tuning material for the detector now.

### What it says

- The settings held on six sources they were not fitted to: no false
  positive for "the same video" in 2160 pairs, 3 for "contains my clip" in
  1184, and the gap between a shared insert and a duplicate where it was.
- The failures gather on footage that barely moves: the bar detector cropped
  it wrongly, which cost the matches the coarse filter seemed to lose and is
  fixed in detector version 2; a short clip of it can be missed or found at
  another moment of the same shot; and positions land anywhere in the shot.
- Limits already written down reappeared on real footage: one match per
  pair, so a duplicate split by inserts can fall under 40 per cent, and
  coverage at 1.25x reading 80 per cent.
- It is one set of six sources, run once. It says the settings were not
  merely fitted to the tuning corpus; it gives no rate for other material.
  Changing a setting because of it, the coarse filter or the detector, makes
  it tuning material, and the change needs another set.

## Second independent validation

Run once, on 2026-09-11, with every setting frozen, detector version 2
included, on ten sources that had no part in choosing the settings or in
fixing the detector. Nothing was tuned on it. The material was chosen to
fill the gaps the first set left.

### The material

| Source | What it is | Cut |
| --- | --- | --- |
| slides | A talk over slides: long still stretches, now and then a moving clip | 5 min from 2:30 |
| aurora | A night timelapse of the northern lights over a coast | 5 min from 0:30 |
| night sky | A night timelapse of clouds over a mountain, a still city skyline along the bottom | 5 min from 1:00 |
| letterboxed | A walk through a shrine, shot at 21:9. Bars of 128 rows were added at the top and bottom to make it 16:9, as a letterboxed upload has them | 5 min from 1:00 |
| video game | A basketball video game with a scoreboard on screen | 5 min from 7:00 |
| episode A, episode B | Two episodes of one animated series. Both open with the same 90 s title sequence | 5 min from the start |
| news 1 | A news clip with a ticker along the bottom, 119 rows that do not change | whole, 94 s |
| news 2, news 3 | Two more news clips | whole, 66 s and 173 s |

The five minute sources get the twelve treatments of the first set. Two
treatments are new:

| Code | Treatment |
| --- | --- |
| `reframe` | Made into a 1080x1920 vertical video: the picture scaled to the width, in the middle, over a blurred copy of itself enlarged to fill the frame |
| `unboxed` | The letterboxed source only: the picture without its bars, at its own 1920x824 |

The news clips are too short for the advertisement and the quotations. They
get `orig`, `seg`, `small`, `bars`, `barstext`, `intro`, `speed` and
`reframe`, and their clip for the second question is the 10 s in the middle.
The advertisement (60 s), the 8 s opening and the text in the bars are new,
and generated as in the first set.

Measured before the run: the two episodes share their first 89.95 s, at 38
to 58 dB PSNR everywhere up to 89.8 s and at 11 dB from 90.0 s. The truth
counts that stretch as shared by any two files that carry it. Nothing else
is shared between sources, and no source loops. After the run, three
corrections were made to the truth about bars. None of them changes a result
for either question. The two clips of the letterboxed source had been
recorded without their bars. The ticker was measured and recorded: over the
whole clip at 2 fps, rows 962 to 1079 change by 19 levels at most, row 961
by 41 and row 960 by 134, so 119 rows, right within a row as for bars. And
the files that have no single right answer were marked, see
[The bar detector](#the-bar-detector-1).

That makes 116 files and 6670 pairs for "are these the same video". For
"does their video contain my clip" the sources are the ten cuts, a 20 s clip
from each five minute source, a 10 s clip from each of the ten, the
advertisement and the opening, 29 in all, against the 116: 3364 pairs.
Build, container and ffmpeg are those of the first set. The signatures, at 5
fps with detector version 2, took 759 s for the 145 files; the comparisons
took 197 s and 187 s.

### Are these the same video

| Class | Pairs | Reported | Constructed coverage | Highest measured |
| --- | ---: | ---: | ---: | ---: |
| same source | 643 | **575** | 38.7 to 100% | 100% |
| share the advertisement | 189 | 0 | 25.0 to 37.5% | 37.8% |
| share the 8 s opening | 45 | 0 | 2.6 to 31.8% | 32.1% |
| share the title sequence | 159 | 0 | 10.0 to 30.0% | 30.3% |
| share a quoted clip | 171 | 0 | 3.2 to 11.1% | 20.4% |
| unrelated | 5463 | 0 | 0 | 22.4% |

No false positive in 6027 pairs. The closest were the two episodes with the
advertisement in front of the same stretch of each. That stretch starts
inside the title sequence, so the advertisement runs straight into shared
footage: 90 s in common, 37.5 per cent of each file by construction, 37.8
measured, 2.2 points under the line. The two episodes with the 8 s opening
in front come next for the same reason. That closeness comes from the five
minute cuts, which start with the title sequence: a whole episode of this
series runs 25 to 30 minutes, where the same 90 s would be 5 to 6 per cent,
or 8 to 10 with the advertisement in front.

Of the 68 misses:

- **43 involve `reframe`.** It was found in every pair for the night sky,
  the video game and two of the news clips; in 11 of 12 for the aurora; in
  10 and 4 of 12 for the two episodes; and in none for the slides, the
  letterboxed source and the ticker clip.
- **20 are the slides' two barred copies**, each against the ten other
  copies that are not `reframe`. The slides are too still for the detector,
  which says so and crops nothing, and uncropped the barred copies match
  none of the ten: not a row. With the crop set by hand, the barred copy
  matches `orig` on 1500 of 1500 frames. The loss is the detector declining,
  not the comparison.
- **5 are structural**, as in the first set: `quote` against `quoteshort`,
  38.7 per cent by construction. Two more of the seven came out at 41.8 and
  44.0.

Without `reframe` and the structural pairs, 510 of 530 were found, and all
20 misses are the barred slides.

Where the 575 were placed: the start lands within a frame on both files in
442 pairs and within five frames in 524, the end within a frame in 486. Most
of the rest sit on the right alignment and are only short, a `reframe` pair
matched over part of its length say. Measured against the alignment itself,
565 of the 575 are within five frames. Of the ten that are not, six are the
slides against their 1.25x copy, where a still slide matches itself at any
offset; one is the night sky's structural pair, reported at 41.8 per cent
264 frames off; and three are 1.25x copies 5 to 21 frames off.

### Does their video contain my clip

| Source | Found | Reported wrongly | Start in their video, median / worst, frames |
| --- | ---: | ---: | ---: |
| cut, 5 min or the whole news clip | 111 of 116 | 0 | 0 / 863 |
| 20 s clip | 95 of 99 | **29** | 0 / 352 |
| 10 s clip | 113 of 123 | **36** | 0 / 535 |
| 60 s advertisement | 21 of 21 | 0 | 0 / 0 |
| 8 s opening | 10 of 10 | **8** | 0 / 0 |

The run was complete: all 29 sources and 116 candidates processed, none
failed. The 19 misses are 13 `reframe` and 6 barred slides. Of the 350 found,
342 sit on the right alignment within five frames. The worst starts are
partial `reframe` matches again, and three wrong places: a 10 s clip of the
video game found 107 s away from where it is in the game's 1.25x copy, the
night sky's 10 s clip 43 s away in its 1.25x copy, and its 20 s clip 81 s
away in its `reframe`. The other five off the alignment are clips inside a
1.25x copy that the vote put at a ratio of 1.0, not 0.8, so the start is off
by up to a fifth of the clip, 8 to 19 frames. The first set had five of
those too.

**The false matches are new.** There are 73, where the first set had 3, and
all come from short sources: 36 from 10 s clips, 29 from 20 s clips and 8
from the 8 s opening. None come from a cut or from the advertisement. 56 are
clips of the two night timelapses, 42 of the aurora and 14 of the night sky;
the aurora's 20 s clip alone was reported inside 24 files of three other
sources, the video game among them. Looked at side by side, the frames share
nothing but a layout, a dark upper half over a lighter lower one. At
`-x 290` two frames like that count as a match, and a 10 s source reaches 40
per cent on 4 s of them.

Why the short sources: unrelated footage supplies a few seconds of
look-alike frames, and the rule divides by the source's length. Over the
2995 pairs that share nothing, the coverage that such a spurious match
reached:

| Source | Highest | 99th percentile | 40% or more |
| --- | ---: | ---: | ---: |
| cut, 66 s to 5 min | 15.3% | 13.3% | 0 |
| 60 s advertisement | 10.0% | 9.7% | 0 |
| 20 s clip | 82% | 60% | 29 |
| 10 s clip | 98% | 60% | 36 |
| 8 s opening | 52.5% | 52.5% | 8 |

On the first set the same tail stopped at a 99th percentile of 32 to 34 per
cent. The frames in those matches pass the frame test only just. Over 8.26
million pairs of frames that share nothing, taken from the sixteen sources of
both sets, the median distance is 334, and 13.6 per cent of the pairs are at
290 or under: one in seven. At 250 it is 2.2 per cent, at 200 0.04 per cent,
and at ffmpeg's own default of 116 none. Every frame of every source has an
unrelated frame within 290. So at `-x 290` a walk through unrelated footage
keeps finding a good frame, and what stops it on a long source is length. On
a short one the coarse filter is left, and the first set showed that it is
the guard there: off, it let through 207 false matches. The two
timelapses' clips passed it against 82 of 102 unrelated files, where the
first set's looping night sky passed it against none and the slides against
none. That is as far as the measurements go: the video game's 20 s clip
passed the coarse filter about as often, 78 of 102, and its frames are as
generic, yet its best spurious match stopped at 35 per cent, where the
aurora's 20 s clip reached 82. Why one clip crosses the line and another does
not was not pinned down beyond that.

What sets them apart from ordinary copies is the mean distance over the
good frames of the match, `meandist`, which can run from 0 to the `-x` of
290: 210.8 to 280 for all 73, against a median of 9 for the 350 true
matches. The true matches above 200 are the ones whose frames really differ
from the source's: `reframe` pairs, two 8 s openings inside copies cropped
for bars or a ticker that the opening does not have, and a clip in a 1.25x
copy voted at 1.0.
They run to 246, so the two ranges overlap from 210 up:

| `meandist` under | True matches found, of 369 | Of them `reframe`, of 27 | False matches |
| ---: | ---: | ---: | ---: |
| no limit | 350 | 14 | 73 |
| 260 | 350 | 14 | 20 |
| 240 | 347 | 11 | 2 |
| 220 | 341 | 5 | 1 |
| 210 | 337 | 3 | 0 |
| 200 | 334 | 1 | 0 |

A ceiling of 210 drops all 73 and 13 true matches: 11 `reframe`, the
opening in the ticker clip's `intro` and the night sky's 10 s clip in its
1.25x copy. It sits just under the lowest false match, so it is fitted to
this set, and what it gives up is most of the reframes. On the first set it
would drop 2 of its 3 false matches and 1 true one, the advertisement in a
copy that version 1 of the detector had cropped; the false match it keeps,
at 72, is the sunset's clip found at another moment of the same shot, which
no ceiling can tell. That rule was found by looking at this set, and the
length test below shows that it does not carry over.

The frames inside the matches say the same. Of the 2933 good frames in the 73
false matches, 94 per cent are between 230 and 290 and none is under 150; of
the 139,360 in the true matches, 89 per cent are under 50. The true matches
that run high have a reason visible in the files: a `reframe`, 203 to 254; a
crop that differs between the two sides, 172 to 216, as when one side lost
its bars or a ticker and the other did not; or a short clip inside a 1.25x
copy, up to 178. Ordinary copies cropped alike stay under 55, and a whole
source against its 1.25x copy under 26.

Exploratory runs on this set, the second question only, with one flag
changed each time (not validation, since the values were picked after
seeing the set):

| Run | True matches found, of 369 | `reframe`, of 27 | False matches |
| --- | ---: | ---: | ---: |
| `-x 290 -b 0.1`, as `find_reuse.py` runs it | 350 | 14 | 73 |
| `-x 270` | 344 | 8 | 13 |
| `-x 250` | 342 | 6 | 2 |
| `-x 230` | 338 | 2 | 0 |
| `-x 210` | 332 | 1 | 0 |
| `-b 0.85`, at least 85 per cent good frames | 350 | 14 | 30 |
| `-b 0.85` and `meandist` under 250 | 350 | 14 | 3 |

Every change that removes the false matches also removes reframes, except
the last row, whose two values sit just past the worst true match of this
set on both measures: 86 per cent good frames at the least, and a `meandist`
of 246 at the most. That is a fit to this set by construction. A stricter
`-x` for `find_reuse.py` alone would leave the first question, where the rule
held, as it is.

### Source length

Measured afterwards on the same material, to find where the false matches
stop. From each of the ten sources, clips of 10 to
120 s around the same centre points, a third and two thirds into each five
minute cut and the middle of each news clip, so that length is the only
thing that changes, were fingerprinted and compared against the 116
candidates at the defaults:

| Source length | Clips | False matches | Of the unrelated pairs | The three look-alike sources | The other seven |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10 s | 17 | 46 | 2.6% | 7.3% | 1 |
| 20 s | 17 | 60 | 3.4% | 9.8% | 0 |
| 30 s | 17 | 56 | 3.2% | 9.1% | 0 |
| 45 s | 17 | 40 | 2.3% | 6.5% | 0 |
| 60 s | 17 | 34 | 2.0% | 5.5% | 0 |
| 90 s | 16 | 9 | 0.6% | 1.5% | 0 |
| 120 s | 15 | 0 | 0 | 0 | 0 |

The three sources are the two night timelapses and the basketball video
game, all dark above and lighter below. Their clips were reported inside
each other's copies and inside the letterboxed shrine walk; clips of the
other seven sources, over all the lengths, made one false match. So the
false matches depend on the material first and on the length second, and
the bound of 20 s that the set itself suggested was an accident of which of
its sources were long: the cuts, the news clips and the advertisement are
not material of that kind. The matches stop between 90 and 120 s, which is
where the longest look-alike stretch measured between two unrelated videos,
41 s, falls under 40 per cent of the source; the 9 at 90 s are all just over
the line, at 40.2 to 45.1 per cent, and at 120 s the highest unrelated pair
reached 33.2. The clips were encoded with VideoToolbox at 12 Mbit/s; at 10
and 20 s they give about the rates of the set's own clips, 2.6 and 3.4
against 3.5 and 4.1 per cent.

The `meandist` ceiling of 210 that removed all 73 false matches of the set
leaves 16 of these 236, the lowest at 143. On 2026-09-11 the owner decided
not to tune the second question for this: README.md states it under Limits.

### The bar detector

The detector's decision was checked against the truth for every file. On the
116 copies:

| Decision | Copies |
| --- | ---: |
| Right: the bars within a row (21), or no crop where there are no bars (65) | 86 |
| The ticker cropped with the bars | 6 |
| `combo`'s bottom bar found 2 rows short | 5 |
| Too still to tell, nothing cropped | 10 |
| No single right answer, decision recorded | 9 |
| Cropped without bars, or bars missed | **0** |

- **No copy was cropped wrongly.** Version 1's failure, still rows read as
  bars when one sampling window falls in a moving stretch, did not recur. The
  slides are that kind of footage, and each of their copies was declined as
  too still or, where the advertisement makes the file move, left whole.
- **The letterboxed source's own bars**, 128 rows, were found exactly on its
  cut, its two clips and every copy that keeps them whole, and as 268 with
  the added bars. The `unboxed` copy correctly got no crop, and it matched
  the letterboxed cut in both questions.
- **The ticker**, 119 rows along the bottom that never change, is cropped
  like a bar on every copy that carries it whole. That is the design, and
  cropped the same way everywhere it cost no match: the ticker clip's copies
  were found in both questions, `reframe` excepted.
- **One wrong crop, on a source**: the night sky's 10 s clip lost 103 rows
  of still city skyline along its bottom edge, the limit version 2 leaves.
  It did not cost a match.
- **The 2 rows short in `combo`** are the encoder: at 3 Mbit/s the two bar
  rows next to the picture carry some of its movement. The first set's
  `combo` copies were exact.
- **The 10 declined** are the slides' copies that are still throughout. That
  is the design, and here it has a price: the barred slides were found by
  nothing, 20 pairs in the first question and 6 in the second.
- **No single right answer** (9): copies of the letterboxed source and of the
  ticker clip with full-frame footage spliced in, the advertisement, the
  opening or another source's clip, so that the bars or the ticker are there
  for part of the length only (7); and their `reframe` copies, whose blur
  spreads the edge over some 80 rows (2). Of the 7, the source's own bars or
  ticker came off in 3 and stayed in 4, as a sampling window did or did not
  fall in the spliced part; both reframes got a crop inside the blurred band.

On the 29 sources: 23 right, 2 with the ticker cropped, the 3 slides
declined, and the night sky's clip above.

### What it says

- "Are these the same video" held on ten more sources: no false positive in
  6027 pairs, two episodes of one series included, which came within 2.2
  points of the line. Without `reframe` and the structural pairs, 510 of 530
  were found, and the 20 misses have one cause.
- "Does their video contain my clip" did not hold for sources under about
  two minutes whose picture has a common light layout. The rule counts the
  share of the source that matched, and unrelated footage of a similar
  layout can supply up to about 40 s of look-alike frames. Two night
  timelapses and a basketball video game made nearly all the false matches,
  at every length up to 90 s; the five minute cuts and the advertisement
  made none. The owner decided to state it rather than tune for it.
- Detector version 2 held on material it was not fixed on: no copy cropped
  wrongly. Still footage with bars is the gap: declined by design, and then
  not found at all.
- New limits: a vertical `reframe` is found in 63 of 106 pairs; a ticker is
  cropped with the bars; in still or repetitive footage a position can be
  off by a minute or more.
- Not covered: clips under a minute, since the three news clips run 66 to
  173 s; bars at the sides; a vertical original.
- Nothing was changed because of this set, so it is still a validation set.
  A crop for still footage would make it tuning material for the detector,
  and that change would need a third set.

## Fixed 5% crop fallback development regression (2026-09-16)

This tests the opt-in Python scanner feature, with unchanged C build 12,
`-b 0.1`, default comparison thresholds and 40% source coverage. It first
compares full frames, then retries below-threshold pairs in both directions:
source cropped 5% per vertical edge / full candidate, and full source / cropped
candidate. New hits require review. Motion and black are not mixed into this
policy. It does not add a C Q1 full-library mode.

The already-seen third-set videos and prior direct-filter probe are now
development data for this feature. No source selection, crop percentage,
threshold or truth label was changed after these results. The original third
batch and its independent evaluation of the previous version remain preserved.
Machine-readable evidence is in
[the crop fallback summary](benchmarks/crop-fallback-2026-09-16-summary.json).

All 32 source queries against 75 candidates completed (2,400 Q2 pairs), using
the frozen frame-domain shared-content truth described in the third-set report:

| Policy | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: |
| Full-frame baseline | 208 | 4 | 9 | 2179 |
| Including review-only fallback hits | 210 | 12 | 7 | 2171 |

**Two misses recovered, eight false positives added.** Recovered pairs were
`dock__source` against `dock__bars` and `dock__barstext`, both covering the
complete common span with zero alignment error. Added false positives were
10/20-second night queries against three light variants, and the 10-second
ocean query against two Alps variants. These are correlated edits, not eight
independent samples. Short-source false-match and position limits remain.
The original black policy had TP 210 / FP 4 on these pairs; fixed cropping is
not a generally better replacement. Keep it off by default and review-only.

A separate full-frame CLI rerun reproduced all 212 baseline hits exactly,
including every field. Fallback retried 2,188 pairs in two directions, for
6,776 comparisons total. On this Linux aarch64 container with jobs=4, the
cached fallback scan took 43.0 s, versus 21.5 s for the separate baseline.
Precomputing both views with three workers took 94.7 s. These single runs are
not a speed guarantee; the public CLI generates second views lazily and
decodes again on the first request, rather than extracting both in one decode.

The narrower O/cropped-C hypothesis was also checked without exporting or
re-encoding C: all 12 original fixed-view signatures matched the earlier
direct-filter C signatures byte for byte. Full O/full C reached the threshold
for 11 sources. The Zion static shot produced only 14/146 frames (9.6%);
cropped O/full C produced 146/146, ratio 1, with matching time positions.
All 12 cross branches reached 100%. This confirms alignment of identical
retained pixels; it is not evidence for arbitrary crops or general accuracy.

Linux `make test`, `make smoke`, and the workspace producer tests passed.
Synthetic smoke cases additionally cover the exact 1000×600 → 1000×540
geometry, portrait rounding, autorotation, and lossless static-picture
recovery. New independent sources are needed before changing this policy.

## Third independent validation (2026-09-16)

This is a new acquisition and pixel-to-signature run, frozen at commit
`793a4c3` (b12, JSON 6), rather than another pass over retained signatures.
The source selection, constructions, truth, code and tool digests were frozen
before invoking the detector or comparator. Neither thresholds, crop logic,
coverage nor speed ranking changed in response to this set. Motion detector 3
and opt-in black-1 each ran the whole inventory, never picking the better mode
per pair. Linux aarch64 used ffmpeg 5.1, 5 signature fps and four comparison jobs.
Q1 used the documented `-b 0.5` flags; Q2 used `-b 0.1`; both applied 40%.

The 12 source videos include three episodes of the same ScienceCasts series;
they are not 12 independent series. Acquired full short originals include a
15-second star field and a 29-second static mountain shot. Other long uploads
use fixed windows selected before testing. The media were normalized to 15 fps,
usually 640×360, with portrait and 4:3 proportions retained. Transformations
include re-encoding, middle cuts, near-black and lettered bars, 0.8×/1.25×/1.17×
full and partial reuse, blurred reframes, metadata rotation and pillarboxing.
Night, ocean and landscape queries use fixed central 10/20/30/60/90/120-second
cuts. Each series episode has full, 10/20-second opening, 30-second middle and
shared-ad versions. The inserted ad is synthetic, not another original source.

| Source | Author | Acquired interval |
| --- | --- | --- |
| [light](https://www.youtube.com/watch?v=HBtdbaSKexU) | NASA Science | Full upload representation |
| [formation](https://www.youtube.com/watch?v=G-NGBRKYPlI) | NASA Science | Full upload representation |
| [moon](https://www.youtube.com/watch?v=sWAN0FwfD5M) | NASA Science | Full upload representation |
| [zion](https://www.youtube.com/watch?v=tayaTlMKmjQ) | Christopher Michael Dortch | Full upload representation |
| [stars](https://www.youtube.com/watch?v=sE-W9f9SaDs) | Freestocks | Full upload representation |
| [slides](https://www.youtube.com/watch?v=PKCMH5KOcxQ) | MIT OpenCourseWare | 1200–1380 s |
| [dock](https://www.youtube.com/watch?v=OIGBHbvr_9Q) | 10minutes2relax | 120–300 s |
| [night](https://www.youtube.com/watch?v=XQ28OXLwFl4) | Night Lights Films - Adrien Mauduit | 180–360 s |
| [ocean](https://www.youtube.com/watch?v=dZ4tvcviweg) | H O R I Z O N   H U N T | 60–240 s |
| [alps](https://www.youtube.com/watch?v=7bOptq-NPJQ) | Nature Relaxation Films | 60–240 s |
| [portrait](https://www.youtube.com/watch?v=TdxHQJH4TKI) | Brionne Olsen | Full upload representation |
| [apollo](https://www.youtube.com/watch?v=S9HdPi9Ikhk) | NASA | 2010–2190 s |

All 107 input entries were processed: 75 candidates and 32 queries. Each mode
built 106 fresh content signatures and reused one byte-identical rotated file
within this batch. All 2,775 Q1 and 2,400 Q2 requested pairs completed per mode;
there were no failed or unprocessed inputs. Complete ledgers, source/recipe
metadata, detector samples and tool output remain in the experiment workspace.
The [machine-readable summary](benchmarks/third-2026-09-16-summary.json) includes
source identities, digests and per-source, scenario and quote-length counts.
Downloaded videos are not included in this repository or required by its tests.

Q1 has two truths: same original source, and longest continuous shared span
covering at least 40% of the shorter file. Shared ads/openings can satisfy the
second without satisfying the first. Q2 truth uses the source-side shared span;
the product still uses its existing matchframes denominator. Truth counts
sampling instants in half-open intervals against actual signature frame counts.

| Question / truth | Crop mode | TP | FP | FN | TN |
| --- | --- | ---: | ---: | ---: | ---: |
| Q1 same origin | motion | 195 | 97 | 40 | 2443 |
| Q1 same origin | black | 196 | 85 | 39 | 2455 |
| Q1 shared coverage | motion | 226 | 66 | 18 | 2465 |
| Q1 shared coverage | black | 225 | 56 | 19 | 2475 |
| Q2 source coverage | motion | 199 | 4 | 18 | 2179 |
| Q2 source coverage | black | 210 | 4 | 7 | 2179 |

Black reduced Q2 misses on this set, but did not improve Q1 coverage misses.
It is not a generally better default. Of 76 entries with a unique geometric
crop truth, motion was correct on 60, over-cropped 2 and abstained on 14; black
was correct on 71 and over-cropped 5. The other 31 have mixed content or lettered
bands without a unique crop answer and are excluded from that correctness tally.

Specific findings:

- Motion removed 73 of 360 picture rows from the unbarred `alps__quote10`,
  missing all ten same-source candidates. Black kept the sky and recovered
  nine; the remaining miss was a blurred reframe.
- Black removed 16 dark picture rows from the unbarred dock source, its
  same-size variants and an added bar, and 8 from the half-size copy. A match
  can survive because both sides lose the same picture; matching success
  does not establish crop correctness.
- Black recovered plain barred copies of the static Zion and dock sources;
  both modes still missed their lettered copies. Both found all five variants
  of the MIT excerpt, which includes changing slides and lecturer shots: this
  is not evidence for an entirely still slide deck.
- Both modes produced the same four unrelated Q2 matches: night queries of
  30/60 seconds against ScienceCasts footage, and a ten-second ocean query
  against a slowed landscape. There were none at 90/120 seconds in this set;
  that does not make either duration a safe boundary.
- Q1 coverage false positives comprise 49 unrelated pairs, 16 shared-opening
  pairs below 40%, and one same-source pair below 40% for motion; black has
  42 unrelated and 14 below-threshold opening pairs. Short opening cards and
  dark/simple footage account for much of the failure, beyond the legitimate
  shared-opening and ad matches counted by coverage truth.
- All eight same-source native portrait / metadata-rotation Q2 positives were
  found and correctly aligned in both modes. All four 4:3 source positives,
  including its pillarbox copy, were found. There is still no side-bar detector;
  these individual successes do not establish general reframe support.

A true-positive pair can report the wrong place. Among coverage TPs, Q1 had
30/31 pairs more than five frames off the true alignment line (motion/black),
with maxima of 142.8/265.6 seconds. These include a pair with a real shared
opening where the reported match lands elsewhere. Q2 had 32/35 such pairs,
with maxima of 46.6/4.02 seconds; another three per mode were aligned but covered
less than 90% of the expected common span. Five frames and 90% are reporting
categories, not promised accuracy. At off-grid speeds, finding the pair still
does not imply exact endpoints. The summary retains alignment and partial
capture separately.

The ScienceCasts opening equivalence is manually annotated as 0–5 seconds,
with about ±0.2-second boundary uncertainty; 10/20-second opening cuts avoid
the 40% ambiguity region. Incidental shared B-roll was not exhaustively labelled.
Apollo's 4:3 picture was unpacked from known side padding in a 16:9 upload before
constructing a pillarbox copy. Crop truth combines visual inspection with known
geometry; naturally dark picture remains picture. Historical `AD.mp4` provenance
is still unknown, so exhaustive source exclusion cannot be proved. Sources,
variants and pairs are correlated; these counts are not a population accuracy
estimate. If this set is later used to tune or fix a detector, retain this run,
reclassify the used sources as development data, and validate on new sources.

## Reproducing

The optional `--crop-mode black` added after P2 uses a separate `black-1`
identity; motion remains version 3 and remains the default. Its development
checks in `tests/black_video.py` generate near-black bars on still textured
footage, dark/all-black negatives, fades, lettering and time-limited bars.
A real cold/hot scan finds the generated barred copy and retains its crop
decision and warning in JSON/HTML. This is a reproducible development check,
not a remeasurement of the deleted slide original or new independent accuracy
evidence; all earlier dataset figures retain their original detector version.

Build 12 adds candidate-level scheduling for a single incremental source.
On Linux aarch64 (GCC 12, eight visible cores), one retained five-minute
source against the other 47 five-minute signatures, at `-b 0.1 -d 9000
-c 60000 -x 290 -i 0 -k 1`, took 59.28 s at build 11 with `-j 4` and
19.64 s at build 12 with `-j 4`: medians of three warm runs after a warm-up.
With `-j 1` the medians were 59.69 s and 59.05 s respectively. Every trial's
CSV and completed ledger pair set agreed. Per-process peak RSS from Linux
`wait4`, including process startup, was 15,712 KiB for all four configurations.
This excludes fingerprinting and is a measurement of this set on this machine,
not a general speed guarantee or a CI timing threshold.

The new build also re-compared the second validation set's retained signatures:
all 6,670 Q1 and 3,364 Q2 requested pairs completed, and all emitted CSV fields
agreed with its archived build 9 run after resolving signature names. This is
a scheduling/input regression; it does not revalidate detector 3 or constitute
a third independent dataset.

What is in this repository and needs nothing private:

- `make test` compares six checked-in signatures, synthetic sources at 30 s
  and 5 fps, against the recorded `compare-longest.csv` output;
  `tests/README.md` has the fixtures and what each pins.
- `make smoke` takes three synthetic clips from video to result through
  `tools/find_reuse.py` twice, with the real ffmpeg, and runs the short/geometry
  and black-mode video regressions described in `tests/README.md`.
- The scenario table above: every clip is `ffmpeg -f lavfi -i <source>` with
  the sources named, `concat` for the joins, `pad=320:240:0:30:black` for the
  bars, `scale=240:136` for the re-encode, then `-vf fps=5,signature=...`.

What is not: the corpus. The six source videos are private, and the scripts
that cut, treat, fingerprint and sweep them live in the experiment area
beside this repository, not in it. Building the same corpus from your own
sources takes the recipes in [The corpus](#the-corpus), the ground truth
rule, and a sweep over `-x` in `longest` with `-i 0 -b 0.1 -k 1`; each run
should record the build that produced it, read from the run's own log, and
the digests of its inputs, which `-s` writes into the ledger for you.

## What this does not tell you

- **One corpus, six sources, and the settings were tuned on it.** The
  treatments are synthetic and applied uniformly. Real re-uploads vary more.
  Later sets measured six, ten and twelve new source videos. The third set
  includes shorter originals, both crop modes and further false matches and
  crop/position errors. Their correlated pairs do not establish a general
  accuracy rate or a safe minimum source duration.
- **One sampling rate.** Everything is at 5 fps. Whether 2 or 3 fps would
  hold up is untested.
- **The original `-d`/`-c` sweep predates the build 7 fix.** Through build 6,
  integer division reduced the distance to 0 or 1 and the defaults rejected
  nothing. Build 7 corrected the scale and direction; the measurements under
  [Build 7](#build-7-the-comparison-changed-and-what-it-did-to-the-corpus) and the independent validations describe the working
  filter. The earlier experiment with both thresholds at 1 is not a usable
  optimisation or evidence about the current defaults.
- **`-m fast` is not in the sweep.** It took the first candidate that
  qualified where `full` at least chose between them, and in every pilot it
  equalled `full` or was worse. Both went in build 10.
- **`matchframes` can exceed the file length by one.** Not because the seed
  is counted twice, which an earlier note here claimed and a check of the
  signature headers refuted: the frame estimate in the corpus's frame table
  was low by one. What is true is that a walk ending on a third consecutive
  bad frame on its way back counts that frame as walked and not as visited,
  so coverage can read 100.07 per cent. Harmless to any fractional threshold.
- **Coverage thresholds swept in post-processing are a lower bound.** A
  candidate selected under loose settings is not necessarily the one that
  would be selected under strict settings, so a real run at the chosen values
  can do better than the table says, not worse.
- **Positions are the walk's, not the editor's.** Measured on synthetic
  clips cut at known points with the ratio at 1.0: exact in three cases of
  four, three frames early in one; wrong on the source side whenever the
  ratio is voted at anything else.
