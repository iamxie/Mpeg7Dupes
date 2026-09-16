# Mpeg7Dupes

Finds visually similar videos by comparing binary MPEG-7 signatures generated
by ffmpeg. It sees through re-encoding, rescaling and trimming, because it
compares what the frames look like rather than what the files contain.

- [What it does](#what-it-does)
- [Install](#install)
- [The recommended flow](#the-recommended-flow)
- [Reading the result](#reading-the-result)
- [Limits](#limits)
- [Long runs](#long-runs)
- [Options](#options)
- [Tests and CI](#tests-and-ci)
- [Where the numbers come from](#where-the-numbers-come-from)
- [Fork notice and license](#fork-notice-and-license)

## What it does

`mpeg7dupes` does not read videos. ffmpeg's `signature` filter turns a video
into a small file of per-frame fingerprints, and this compares those files,
every pair of them, reporting for each pair how many frames matched and
where the match sits in each file. Generating the signatures is the slow
part by a wide margin: on 96 files it took 19 minutes against 45 seconds for
the comparison. Keep the signatures.

It answers two questions, and they are read differently:

- **Are these two videos the same?** Compare a library against itself and
  keep the pairs where the matched frames cover enough of the shorter file.
- **Does their video contain my clip, and where?** `tools/find_reuse.py`
  runs the whole pipeline for that: fingerprints, caches, crops bars, compares
  and prints where in their file your clip starts.

## Install

Linux only. Packages, on Debian or Ubuntu:

```sh
sudo apt-get install build-essential git libavcodec-dev libavfilter-dev ffmpeg
```

libavcodec and libavfilter are needed for their headers only; the binary
calls into neither. Your GCC must support OpenMP, or the comparison runs on
one core.

**slog.** The logging library, 1.9 or newer, and having it installed already
is not enough: a machine that built this project before may still have 1.6
in `/usr/local`, and that is the most common way the build fails. Install the
pinned version over the top of whatever is there:

```sh
git clone --depth 1 --branch v1.8.49 https://github.com/kala13x/slog /tmp/slog
cd /tmp/slog && make && sudo make install
```

`v1.8.49` is what CI and the Dockerfile build against; its header declares
itself 1.9.49, since slog's tags and its version macros disagree, and the
header is what the build checks. `grep SLOG_VERSION_MINOR
/usr/local/include/slog.h` prints 9 when it is right and nothing at all when
1.6.2 is there, which defines no version macros. A build against an old slog
stops on the first source file with `#error "slog 1.9 or newer is required"`
and the command above.

Then:

```sh
git clone https://github.com/iamxie/Mpeg7Dupes
cd Mpeg7Dupes
make release -l"$(nproc)"
sudo cp bin/mpeg7Dupes.elf /usr/local/bin/mpeg7dupes
```

`make release` compiles with `-march=native`, so the binary is tuned to the
machine that built it and may die with SIGILL on another CPU. To copy a
binary around, `make static` builds one with no runtime dependencies and
without that flag; [Releases](https://github.com/iamxie/Mpeg7Dupes/releases)
carries such binaries for x86_64 and aarch64, `v*` being versions somebody
decided to stand behind and `nightly-<commit>` daily builds of `master` that
passed the same tests. `docker build -t mpeg7dupes .` builds an image with
ffmpeg in it as well; `--build-arg SLOG_REF=` changes the slog pin.

## The recommended flow

Three steps. `tools/find_reuse.py` does all three for the second question;
for the first, do them yourself.

**1. Generate one signature per video, all at the same rate.**

```sh
ffmpeg -i input.mkv -vf "fps=5,signature=filename=input.bin" -map 0:v:0 -an -f null -
```

Every signature must use the same `fps`. The filter emits one fingerprint per
frame it receives and knows nothing about time, so fingerprint 300 sits at
ten seconds in a 30 fps signature and at sixty in a 5 fps one; mixing rates
compares different moments and finds nothing. 5 is the measured balance
between storage, time resolution and the shortest clip that can be found;
see [Limits](#limits) before going lower. Only binary signatures are read,
never `format=xml`.

Crop bars off first. A letterboxed copy and a clean one do not line up,
because the bar shifts the picture inside the frame; `tools/detect_bars.py`
prints the crop for a video, and `find_reuse.py` applies it in the same
ffmpeg pipeline that takes the fingerprint.

**2. Compare.** One path per line in a list file:

```sh
find sig -name '*.bin' | sort > siglist.txt
mpeg7dupes -l siglist.txt > dupes.csv 2> run.log
```

The defaults since build 4 are the measured settings, `-f csv -m longest
-x 290 -i 0 -k 1 -b 0.5`; on an older build, spell them out. Results go to
stdout and the log to stderr. Every pair in the list is compared, so the work
grows as n²/2: 600 signatures means 179,700 comparisons, spread across every
core.

**3. Decide.** For "the same video", keep the pairs where

```
matchframes / min(frames in file A, frames in file B) >= 0.40
```

The frame counts are in each signature's header and are not in the CSV.
That threshold sits in the empty band the benchmark found between pairs that
merely share an advertisement and genuine duplicates; the reasoning and its
limits are in [benchmark.md](benchmark.md).

For "does their video contain mine":

```sh
uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads
```

```
sources     1
candidates  128, signatures cached in ./downloads/.signatures
  signatures 128/128

./downloads/a.mp4   used mine.mp4, starting at 02:53
./downloads/k.mp4   used mine.mp4, starting at 01:10

scanned 128 candidates against 1 source, 2 matches over 40%
```

It needs ffmpeg, `mpeg7dupes` on PATH or `--mpeg7dupes`, and uv or a
Python 3.11 interpreter. `--source` also takes a folder; sources are compared
against every candidate and never against each other. `--json` keeps the
whole run, and `tools/render_report.py` turns that file into a page that
plays the two videos side by side, parked just before the join.

## Reading the result

### Are these two videos the same

One CSV row per pair with a match; a pair scoring 0 is not printed at all,
so a missing row does not mean the pair was never compared.

| Column | Meaning |
| --- | --- |
| `score` | Votes for the winning alignment, measured from whichever file is first. Not a length |
| `matchframes` | Frames of the second clip the walk covered, including up to three bad frames tolerated at each end. This, over the shorter file's frame count, is coverage |
| `goodframes`, `totalframes` | Numerator and denominator of the `-b` test, so a stricter `-b` can be applied to a finished CSV |
| `offset` | Where the seed pair sat inside its Hough window. A diagnostic, not the shift between the clips |
| `framerateratio` | The speed of the second clip relative to the first, as the alignment voted it, on a grid of thirtieths. At any value but 1.0 `matchframes` counts the frames of the slower clip; see [Limits](#limits) |
| `meandist` | Mean frame distance over the match, lower is closer |
| `time 1 [s]`, `time 2 [s]` | The frame each side was seeded on. Inside the match, not at either end |
| `begin 1 [s]`, `end 1 [s]`, `begin 2 [s]`, `end 2 [s]` | The first and last frame the walk accepted in each file, at the sampling rate. `end` is the time of the last matched frame, not the frame after it |
| `whole` | 1 when the walk reached a beginning and an end. Read the next paragraph before trusting it |

A run over signatures sampled at 5 fps, where the second file was a 240x136
re-encode of a 320x180 original with five seconds trimmed from each end:

```
original.bin,reupload.bin,1056,550,550,550,-26,1.000000,12.57,6.00,1.00,5.00,114.80,0.00,109.80,1
```

`matchframes` 550 against a 550-frame signature says the shorter clip matches
end to end; `begin 1` 5.00 places it five seconds into the original.

**`whole` is not "these are the same video."** It is set when the walk
reached a beginning and an end, and those need not belong to the same file:
material at the head of one clip and the tail of another satisfies it. On
the benchmark, two unrelated clips carrying the same 70 second advertisement
came back with `whole` 1, 351 matched frames, and a score higher than any
genuine duplicate. Coverage is the measure; `whole` is a hint.

Which file is first in a row is not fixed, because the comparison runs on
every core and rows are written as they finish. `score` and `offset` change
with the orientation; `matchframes` and the boundaries do not.

Paths holding a comma, a double quote or a line break are quoted in the CSV
with inner quotes doubled; any other path is written as it is. CSV is the
only output; the `beautiful` tree of build 7 and earlier was removed in build
8, since it assumed rows arrive in order and `tools/render_report.py` is the
readable view now.

### Does their video contain my clip

Every requested source and candidate stays in the JSON record, including
files that failed and candidates skipped because they are source paths.
A candidate is `matched`, `checked`, `failed`, `skipped`, or `not_compared`.
`checked` means it was compared against every requested source and did not
reach the threshold. If some sources fail, the record names the sources
actually compared in `comparison.sources_completed`; a match can still be
reported, but the candidate's `comparison_complete` is false. Unfinished
comparisons never become misses.

Exit 0 means the requested scan completed, whether or not it found a match.
Exit 1 means some file processing failed but usable comparisons completed.
Exit 2 means invalid settings or tools, no usable comparison, or a comparison
failure. A later comparison failure preserves matches from earlier completed
sources. Once the input inventory is collected, fatal errors also write a
record when `--json` is supplied, so an earlier successful record is not left
looking like this run. JSON is replaced atomically; a write failure leaves
the previous file intact and reports exit 2. Records use `find_reuse/6`;
`render_report.py` reads versions 3 through 6 and shows unfinished work.

Every processed video carries its crop decision: `disabled`, `detected`,
`none` or `uncertain` (`unknown` for legacy metadata). These decisions and
warnings appear on both fresh and cached scans. Short sources get the known
simple-layout false-match warning; an uncertain crop warns that barred copies
may be missed. All records and pages state the low-motion/repetition position
limit and unsupported reframe limit. A checked candidate means **no match
reaching the threshold was found**, which does not rule out reuse.

`settings.crop_mode` and each processed video's `crop_mode` say `motion`,
`black`, or `disabled`. The selected mode is also in `tool.detector.mode`;
the cache detector key is `3` for motion and `black-1` for black. Older
records describe motion cropping, or disabled cropping, and remain readable.

`settings.comparison_args` contains the actual C flags, including the wrapper's
`-b 0.1 -d 9000 -c 60000`; `tool` records the binary SHA-256, scanner and current
detector code identities. Each video's `content_hash` is BLAKE2b-128 and its
`signature` records the filename, SHA-256, stored detector version and original
ffmpeg version. The stored generator version is retained on cache hits; it is
not replaced by the ffmpeg currently on PATH. A legacy missing identity stays
unknown. `jobs_requested` is the requested count (0 means automatic); C logs
the effective count after limiting it to available cores.
Recording SHA-256 reads each signature, including on a cache hit; it does not
decode or rehash an unchanged video.

The record preserves the paths supplied to the scanner and saves their
resolution base in `path_base`. A report can be written elsewhere:

```sh
uv run tools/render_report.py reuse.json --out /path/to/reports/reuse.html
```

Players resolve from the original scan folder, then use paths relative to the
HTML file. Videos are not copied. For a legacy record without a saved base,
pass `--path-base /original/scan/folder`; without it the renderer warns and
uses the report folder, as older versions did.

Settings are checked before creating output: finite positive fps, coverage
from 0 to 100, nonnegative integer jobs and valid C integer ranges. TOML
booleans must be booleans; unknown keys, an unreadable or missing explicit
`--config`, and malformed TOML are errors. Relative executable paths are
resolved before any subprocess changes directory.

The script divides by the source's own frame count, which is the question
being asked, and reports only that the threshold was passed, never a
percentage, because the figure runs a little high: clips using 86, 50 and 30
per cent of a source came back as 88, 54 and 33, the walk carrying a few
frames past each end of what is really shared. `--min-coverage 40` therefore
fires at around 36 per cent of real use, which errs towards looking at a few
extra videos rather than missing one. For a source under about two minutes
it can also fire on unrelated footage that only looks alike; see
[Limits](#limits).

The start is the first frame the comparison accepted in their file, and the
record also carries the span taken out of yours. Measured on synthetic clips
cut at known points, with the speed ratio voted at 1.0: exact on both sides in
three cases of four, and three frames early on both sides in the fourth. The
smoke test checks a copy cut at five seconds lands within a second of it.
That is the accuracy to expect at 5 fps; the boundaries are the walk's, not
the editor's. When the ratio is voted at anything but 1.0 the source-side span
is not to be trusted and the line says so. On footage that barely moves or
repeats, the times can be off by a minute or more while the match itself is
right; see [Limits](#limits).

`--no-coarse-filter` passes `-d 10001` to the comparison, which turns off the
coarse filter described under [Options](#options). It is for checking the
filter, not for everyday use. On the benchmark corpus the filter kept every
true match and took about two thirds off the comparison time. On the first
independent validation set it missed 7 of 256 matches, all on a sunset shot
from a locked-off camera and 5 of them through a bar-detector fault since
fixed, and turning it off brought those back along with 204 false ones:
clips of 10 and 20 s, and an 8 s opening, reported inside unrelated videos
at 40 to 100 per cent. For short sources the filter is a
guard, so off is not the safe side. Expect the comparison, the whole cost
once the signatures are cached, to take about three times as long;
fingerprinting is unaffected. The JSON record says which way a run was made,
in `settings.coarse_filter`, and so does the page.

The signature store, a SQLite index beside a directory of `.sig` files,
identifies a video by what it contains, so a renamed, moved or copied file
keeps its signature and a second run over an unchanged folder reads no video
at all; the smoke test holds it to that. Several tools share one store when
they are given the same `--db` and the same `--sig-dir` and use the same
`fps` and bar setting, which are part of a signature's identity; the index
names files relative to the directory, so the index alone finds nothing, and
the directory alone cannot rebuild the index, since the crop applied and the
duration live only there. New signatures are validated at a temporary name,
then published with a unique `.gen-<id>.sig` filename. The index switches to
that file in a short transaction. A failed `--overwrite` keeps the old file
and row; active scans can continue reading their original signature. Rebuilds
can leave unreferenced generations in this cache; there is no automatic garbage
collection. Keep the index with the directory and use the index to select
signatures, rather than comparing every generation found by a glob.

Concurrent producers on one machine use a file lock per cache key, recheck the
cache after waiting, and do not hold a SQLite write transaction during ffmpeg.
Initialization has its own brief lock. Lock files live beside the index in
`.<index-name>.locks`; do not remove them while producers are running. This
requires both current producers to share the same index and directory on a
local filesystem; cross-machine/network-filesystem locking is not promised.
Each scan owns its comparison lists. Source size, timestamps and file identity
are checked from hashing through decoding; a change discards that attempt.
The unchanged-file fast path still trusts size and mtime, so deliberately
restoring both can evade it; use `--overwrite` to force reidentification.

FPS names now preserve the exact float key. Unambiguous integer-fps cache
entries remain usable; old rows that shared a rounded filename are invalidated
as a group. Bar detector version 3 selects the first video track consistently
with signature generation, accounts for display rotation, and treats a failed
sampling window as a file failure. Its motion thresholds are unchanged, but
its new identity deliberately rebuilds older cropped signatures once. Uncropped
signatures keep their existing detector-independent identity. The cheap cache
check validates supported header flags and byte counts; full data validation
is performed by the C loader when comparing.

## Limits

Measured ones. Each is either in a test or in
[benchmark.md](benchmark.md); the synthetic ones were checked with build 5.

**Short clips are missed silently.** At 5 fps on synthetic clips, a clip
matches a copy or a half-width re-encode of itself from 10 frames, 2 s. An
excerpt inside a longer clip is found end to end from 50 frames, 10 s, only
partially between 15 and 40 frames, and not at all below that. Real footage
with less texture needs more. Nothing reports this; the pair simply does not
appear. Raise `fps` if the library holds short clips.

**A source under about two minutes can be found in footage it is not in.**
"Does their video contain my clip" reports a candidate when the match covers
40 per cent of the source, and at `-x 290` two frames that share only a
light layout, a dark sky over a lit foreground say, count as a match.
Between two unrelated videos there can be a stretch of about 40 s in which
every frame is just close enough. On the second validation set and a length
test on its sources, sources cut from two night timelapses and a basketball
video game, footage with that kind of layout, were reported inside unrelated
videos of a similar layout at every length measured from 10 to 60 s, in 5 to
10 per cent of the unrelated candidates; at 90 s in 1.5 per cent, all just
over the line at 40 to 45 per cent; at 120 s and at five minutes in none.
Sources of other material almost never were: 1 false match over all the
lengths. `meandist`, lower is closer, runs high on these matches, but no
ceiling on it removed them without also losing reframes and copies cropped
differently, so nothing filters them out. When a source is short and its
picture is plain, look at the match on the page before you act on it; the
page plays both videos at the join.

**Featureless footage matches nothing, itself included.** Synthetic solid
colours and test patterns compared against each other produced no row at all
at the defaults, and neither did a solid colour against its own half-width
re-encode. A static but textured clip, colour bars, did match its letterboxed
copy end to end. So static footage was not seen to cause false positives,
but a genuine duplicate that is nearly featureless can be missed outright.
Real low-motion footage sits in between. On the first validation set a sunset
from a locked-off camera and a night sky were recognised in their own copies,
and a 10 s clip of the sunset was missed inside other material; where
matches land in such footage is the next limit. A talk show at a dinner
table behaved like any other footage.

**On footage that barely moves or repeats, the start and end can be wrong;
whether it matched is still right.** When the picture looks the same at
different moments, a shot that barely moves, a loop, a court seen from a
fixed camera, the comparison can line the two files up at a moment that
looks right but is not. On the validation sets, times off by more than a
few seconds came only from such footage, and most often when the copy had
also been changed, sped up or reframed: on the second set a slide talk
against its 1.25x copy was placed 18 to 30 s off, a 10 s clip of a
basketball video game 107 s off inside the game's 1.25x copy, and a 20 s
clip of a night timelapse 81 s off inside a vertical reframe; on the first
set a sunset from a locked-off camera was placed up to 90 s off. Every one
of those matches was a true one: the
answer to "are these the same video" or "does their video contain my clip"
was right, and only the times were wrong. One case goes further: when their
video holds another moment of the same still shot, a clip of that shot is
found there too, as a clip of the sunset was, 50 s away from where it was
cut. On such footage, take the times as a hint and check them on the page.

**A shared opening is a match, and coverage cannot tell it from a copy.**
Two unrelated 60 s clips with the same 8 s opening matched on those 43 frames,
14 per cent of either, so the 40 per cent rule leaves them alone. The same
opening on two 20 s clips is 43 per cent, and the rule calls them duplicates.
Coverage measures how much is shared, not what: the benchmark's separation
holds because its advertisement is short against the body it is attached to,
and an insert longer than about two minutes on a five minute body would turn
it the wrong way round. The boundary columns say where the shared part sits,
an opening landing at the start of both; use them.

The rule of thumb: two videos are called the same once the longest stretch
they share reaches 40 per cent of the shorter one, so two episodes of a
series meet it only when an episode is shorter than two and a half times
the longest stretch they share, the opening say. The opening and the closing
do not add up, since only the longest shared stretch counts. On the second
validation set, five minute cuts of two episodes that both start with the
same 90 s title sequence, with the same advertisement put in front of each,
came to 37.8 per cent; whole episodes of that series, 25 to 30 minutes long,
share 5 to 6 per cent.

**Copies at another speed are found, to the ratio's grid.** Since build 7
the walk keeps the two clips in step at the ratio the alignment voted, so a
1.25x and a 0.8x copy of a 60 s synthetic clip both come back whole, 240 of
240 and 300 of 300 frames, where build 6 lost the first within a few frames.
The ratio is voted on a grid of thirtieths, so a position on the faster side
can be off by the length of the match times the gap between the true ratio
and the grid: the 0.8x copy was placed 2.6 s in where 0 is right. And
`matchframes` counts the frames of the slower clip, so `find_reuse.py`,
which divides by the source's frame count, reads 80 per cent for a 1.25x
copy that holds all of the source; it says so on the line. Build 6 also
voted the wrong ratio for a short quotation, 0.07 for a 12 s extract inside
other material, and misplaced its source-side span; that came from the
candidate scan covering only half of the accumulator, and the same
extract now votes 1.0 with the span exact.

**A short clip inside a sped-up copy can come back at a ratio of 1.0.** On
the two validation sets every 1.25x copy of a whole source was voted at 0.80
and placed exactly. A 10 or 20 s clip inside such a copy was found every
time, 29 of 29, but 13 of them came back at 1.0, all on footage whose frames
two seconds apart still count as alike: a still shot, loops, timelapses,
slides, a video game and one news clip. The comparison keeps the longest of
its candidate alignments. At 1.0 a 10 s clip is walked in 50 steps, one per
frame of its own, and ends 2 s out of step with their copy; at the right
ratio of 0.80 it takes 40 steps and stays in step. On such footage both
walks hold, so the longer, wrong one wins. The answer is still right. What
is wrong: the start in their video is off by up to a fifth of the clip, 2 s
for a 10 s clip and 4 s for a 20 s one; the line does not say that their
copy is sped up; and coverage reads 100 per cent rather than 80. Two clips
of repetitive footage were placed much further off, which is the limit on
footage that barely moves or repeats, above. Left as it is on purpose: the
answer is right and the times are close.

**One match per pair.** The program says whether a source was used and
where its longest use sits. It does not list every use: a source cut into
their video in three places is reported once, at the longest of the three.
That is the intended scope, not a gap to be closed. It reaches duplicates
too: two copies of one video that each had something spliced in at a
different place share their body in pieces, and if no piece reaches 40 per
cent of the shorter file the pair is not reported. On the two validation
sets 8 of 11 such pairs, whose longest shared piece was 38.7 per cent of the
shorter file, were not.

**Bars on a still picture.** When a video barely moves at all, the default motion
detector says it cannot tell and nothing is cropped. Up to version 1 it went
wrong on footage that is still apart from a short moving stretch, a clip
spliced into a static shot say, taking still rows of the picture for bars:
on the first validation set a sunset with a 10 s clip in it lost 482 of its 1080
rows, and two more files lost 274 and 136, none of them with bars. Version 2
also asks that a bar look the same in every sampled window, which leaves
those rows alone and changes nothing on the 96 videos it was tuned on. What
remains: when the moving stretch's still edges show what the still shot
shows there, black in both say, nothing sampled tells them from a bar; and
still picture along a whole edge of moving footage reads as a bar, as a city
skyline under a night timelapse did, 103 rows of it. On the second set,
material version 2 was not fixed on, it cropped none of 116 copies wrongly.

**A slide talk with bars added may not be found at all.** When the middle of
the frame barely moves for the whole video, as in a talk that stays on its
slides, the motion detector cannot tell bars from picture and crops nothing. A
copy with bars added at the top and bottom is then compared with its bars
on, and the bars shift the picture inside the frame. On the second
validation set a slide talk's two copies with bars matched none of its ten
other copies, not a single frame, in either question; with the bars cropped
by hand, the same copy matched the original on 1500 of 1500 frames. It is
not every still video: the first set's two still shots, a sunset and a night
sky, were found with their bars on. Do not read a miss against a barred copy
of a video that barely moves as a clean result.

For plain black bars on this kind of footage, explicitly choose
`--crop-mode black` in `find_reuse.py` (`crop_mode = "black"` in its TOML),
or `--mode black` in `detect_bars.py`. Motion remains the default; there is
no automatic fallback. `--no-crop-bars` disables either mode. Both source
and candidate use the chosen mode, with separate cache keys, so switching
back to motion reuses its existing signatures.

Black mode uses ffmpeg's `cropdetect` on full-resolution, full-range grey,
with black level 16/255 and no bright outliers. It keeps the union of picture
bounds across the same sampled windows as motion, rounds toward retaining
picture, and only crops top/bottom. All-dark or excessively narrow picture
is uncertain and remains uncropped. Near-black compressed bars and a still
textured copy are covered by generated tests, including a complete cold/hot
scan. Dark picture edges can still be mistaken for bars, lettering stops the
crop, and changes outside the samples can be missed. Review the crop; JSON,
terminal and HTML carry this warning. The [third independent set](benchmark.md#third-independent-validation-2026-09-16)
now measures motion 3 and black-1 on newly acquired footage. Black recovered
some plain barred still copies, but also removed dark picture from a dock shot;
lettered copies still failed. Motion over-cropped an unbarred short landscape.
Neither mode is universally correct. The archived slide original remains untested.

**A band that never changes is cropped like a bar.** The motion detector looks for
rows that do not move, so a news ticker or a caption strip across the whole
width that stays the same for the whole video comes off with the bars: on the
second set a 119-row ticker, on every copy that carried it whole. Cropped
the same way on every copy, it cost no match; what it takes out of the
comparison is whatever the band shows. A scoreboard over part of the width
is not cropped, since the rest of those rows move.

**Vertical reframes are not supported.** A 16:9 video made into 9:16, the
picture across the middle over a blurred enlargement of itself, is a common
way to repost to phones, and nothing here undoes it: the blurred backdrop is
most of the frame and is compared as if it were the picture. On the second
set such copies were found in 63 of 106 same-video pairs and 14 of 27
contains-my-clip matches, and never for a slide talk, a letterboxed source or
a clip with a ticker, so a miss against a reframed copy says nothing. Other
ways of turning a video on its side, a crop to 9:16 say, were not tested.
Bars at the left and right, a 4:3 picture in a 16:9 frame say, are not looked
for at all.

**Validated twice, on sixteen sources it was not tuned on.** The first set,
a talk show, an animation, sports, a star field, a sunset and a night sky,
72 files in twelve kinds of edit, compared once at the defaults: 382 of 396
same-video pairs found with none of 2160 other pairs reported, and 249 of
256 contains-my-clip matches found with 3 false ones among 1184 other pairs.
21 of those 24 errors involve the sunset, a locked-off shot that barely
moves, and 17 a file cropped as described above. With version 2 of the
detector the same set gives 394 of 396 and 254 of 256 with 2 false, but that
detector was fixed by looking at this set, so those numbers are not
independent. The second set, ten sources and 116 files with every setting
frozen, detector version 2 included: 575 of 643 same-video pairs found with
none of 6027 other pairs reported, the misses being vertical reframes, the
barred copies of a still slide talk and the structural pairs above; and 350
of 369 contains-my-clip matches found, but with 73 false ones among 2995
other pairs, all from its sources of 20 s and less; a length test on the same
material later found the risk up to about two minutes, as the limit above
says. benchmark.md has the tables. The settings held for the first question
on material they were not fitted to. Neither set says what they will do on
yours.

**Performance.** The second signature of every pair is read and parsed from
disk again, so a file is parsed once per comparison it takes part in.

## Long runs

**Threads.** Every core by default; `-j N` limits it. Since build 12, a single
`-n` source is loaded once and its candidate pairs are distributed across
workers. Full-library comparisons retain the outer-loop schedule. The Python
wrapper invokes C once per source, so it does not multiply `--jobs` by the
number of sources. `-vv` includes the worker and pair indices for tracing.
**Progress.** `-v` reports every 1 per cent (at least 50 pairs) with a rate and
an ETA.

**Interrupting.** Pass `-s` and every pair is recorded as it finishes,
whether or not it matched. Run the same command again after an interruption
and it picks up where it stopped; a resumed run prints only the pairs it
compares itself, and says so on stderr, so append its output to the earlier
one:

```sh
mpeg7dupes -l list.txt -s dupes.ledger > part1.csv
# killed partway through
mpeg7dupes -l list.txt -s dupes.ledger >> part1.csv
```

A pair is written to the output, flushed, and then recorded in the ledger.
A process killed between those steps can leave completed output unrecorded;
resuming can therefore repeat several pairs. With the same inputs and settings,
and output correctly appended, a process interruption does not skip those
pairs. An unfinished final ledger line is discarded before appending. One
process at a time may use a ledger; a second process is refused.

This is process-interruption recovery. Output and ledger are separate files
and are not synchronously committed together: reboot, power loss or storage
failure is outside this guarantee. Keep the output along with the ledger.
Use a CSV parser to remove repeated headers and pairs, including paths with
commas or quotes:

```sh
python3 - <<'PYCSV'
import csv
with open("part1.csv", newline="") as src, open("dupes.csv", "w", newline="") as dst:
    rows = csv.reader(src, strict=True)
    header = next(rows)
    writer = csv.writer(dst)
    writer.writerow(header)
    seen = set()
    for row in rows:
        if row == header:
            continue
        if len(row) != len(header):
            raise ValueError("incomplete CSV row; inspect the interrupted output")
        pair = tuple(sorted(row[:2]))
        if pair not in seen:
            writer.writerow(row)
            seen.add(pair)
PYCSV
```

With `-s`, paths containing Tab or CR/LF, or starting with `#`, cannot be
represented by the ledger and are refused before it is opened. Spaces,
commas, quotes and Unicode names remain supported. A relative `#name.bin`
can be spelled `./#name.bin` instead. Build 11 records floating-point settings
at full precision; different `-b` values cannot silently share a run record.

The ledger records what it was written for, in lines starting with `#`: the
build, a digest of the binary, every setting that changes the output, and the
size and content digest of each input. A resume that is not the same run is
refused before a single pair is skipped, with exit status 1 and a message
saying what differs and how to go on. The pairs recorded there, and the
output written beside them, were made with the other build or settings and
cannot be continued with this one: the rows would not be comparable, since
a build can change what a column means. Either go back to what they were
made with, or move the ledger and its output away and compare the whole
list again. A ledger from build 4 or earlier has no record: it is reused
with a warning and given one. If the output cannot be written, a full disk
say, the run stops with exit status 1 before recording the pair.

**Adding files later.** Keep the ledger, add the new signatures to the list
and run again; only the new pairs are compared. `-n` does the same for a
single batch without keeping a record:

```sh
mpeg7dupes -l old.txt -n new.txt > new_dupes.csv
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `-l`, `--file_list` | | Read signature paths from a file, one per line. Blank lines are skipped, CRLF endings stripped, and the last line needs no newline; a path over 319 bytes is refused rather than cut |
| `-n`, `--incremental_file_list` | | Compare this list against itself and against `-l` |
| `-s`, `--ledger` | | Record compared pairs here and skip the ones already in it; see [Long runs](#long-runs) |
| `-f`, `--output_format` | `csv` | `csv`, the only format since build 8 |
| `-j`, `--jobs` | every core | Limit the run to this many cores |
| `-v`, `--verbosity` | | Repeatable: `-v` progress, `-vv` per-pair detail, `-vvv` every fingerprint, which is enormous |
| `--version` | | Print the build and exit. Every run also logs it |
| `-m`, `--lookup_mode` | `longest` | `longest`, the only mode since build 10: it ranks candidates by match length, the closer one winning a tie, and does not stop early. `full`, which stopped once one walk had reached an end in each file, and `fast`, which took the first candidate that qualified, were removed; benchmark.md keeps the comparison |
| `-i`, `--thDi` | 0 | Minimum length in frames for a match to be reported. Shorter candidates are skipped, longer ones reported in full |
| `-k`, `--minimum_score` | 1 | Rows scoring below this are not printed. The score counts votes, not frames; the old default of 49 hid genuine duplicates over 900 frames long |
| `-x`, `--thXh` | 290 | Frame similarity threshold. Not above 290: from 310, black bars alone match unrelated videos to each other |
| `-b`, `--thIt` | 0.5 | Required ratio of good frames to all frames walked, between 0 and 1. A reported candidate never falls below about 0.17, so values up to that keep every candidate |
| `-d`, `--thD`, `-c`, `--thDc` | 9000, 60000 | The coarse filter, which decides whether a pair of segments is worth walking at all. The five words of two segments are compared by Jaccard distance in ten-thousandths, 0 for the same set and 10000 for sets that share nothing; a pair is walked unless three or more words are at or beyond `-d`, or the five distances together exceed `-c`. At the defaults that rejects a pair only when three words share a tenth or less of their bits, which on the benchmark corpus keeps every true match and drops most of the comparison time (see [benchmark.md](benchmark.md)). `-d 10001` turns it off. Up to build 6 the filter never rejected anything |
| `-t`, `--signature_type` | `binary` | Only `binary` works |

A value an option cannot hold is refused with a message naming the option:
`-b nan`, `-b 1.5`, `-i 1.5`, `-x 12abc`, `-j abc`, `-k 0`, `-m csv`. A
signature file that is empty, truncated or not a signature stops the run
naming the file; it is never reported as a pair that did not match.

## Tests and CI

```sh
make test     # builds the binary, then the unit tests and the suites; no ffmpeg needed
make smoke    # three synthetic clips through find_reuse.py, twice; needs ffmpeg
```

`make test` always builds and tests the source as it is now, never what was
left in `bin/`; `STATIC=1` tests the static binary and `DEBUG=1` runs under
AddressSanitizer. `tests/README.md` says what each layer covers, which tests
pin a known defect on purpose, and what is not covered.

CI runs both on every push and pull request, on x86_64 and aarch64, holding
no write permission. The release workflow builds the static binaries nightly
and on `v*` tags, runs the same tests on the binary it is about to publish,
and is the only job that can write. Both, and the Dockerfile, build slog at
the same pinned ref. The video benchmark runs in neither; it takes hours on
private material.

## Where the numbers come from

[benchmark.md](benchmark.md). One corpus of 96 videos built from six sources
by eight treatments each, compared as all 4560 pairs with build 2, is what
the defaults, the 40 per cent rule and the cropping were tuned on; with the
bars cropped it finds all 720 true duplicates with no false positives. Build
5 was rerun on it and gives the same matched frames pair for pair, and
build 7, which changed the comparison, still finds all 720 with no false
positives, 670 of them frame for frame as before and the rest within a few
frames, in about a third of the time. It is a
regression set now, not a validation set. Later sets used six, ten and twelve
new source videos. The third froze build 12, motion 3 and black-1 before new
fingerprinting and comparison; it found further crop, short-source false-match
and position limits. benchmark.md reports each set separately, and
[Limits](#limits) explains how to interpret results. The synthetic checks behind
the limits are there too, with the commands to reproduce them.

## Fork notice and license

A fork of [Jacotsu/Mpeg7Dupes](https://github.com/Jacotsu/Mpeg7Dupes), taken
at upstream commit `42604d0` in September 2026. Since then: the comparison
loop actually runs on every core, results go to stdout and logs to stderr,
the CSV carries frame counts, boundaries and the speed ratio, the defaults are
the measured ones, options refuse values they cannot hold, malformed
signatures stop the run, `-s` makes a run resumable, and the Python tools,
the tests, the CI and the static builds are new. The groundwork is all
upstream's; if this is useful, consider supporting its original author at
[Liberapay](https://liberapay.com/jacotsu/donate), which funds Jacotsu and not
this fork.

AGPL-3.0, unchanged from upstream. See [LICENSE](LICENSE).
