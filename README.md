# Mpeg7Dupes

Finds visually similar videos by comparing binary MPEG-7 signatures generated
by ffmpeg. It sees through re-encoding, rescaling and trimming, because it
compares what the frames look like rather than what the files contain.

- [What it does](#what-it-does)
- [Install](#install)
- [Quick start](#quick-start)
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

## Quick start

Choose the task below, copy its commands, then open the HTML report. You can
read [The recommended flow](#the-recommended-flow) later for the reasons
behind the settings.

Before starting, complete [Install](#install) and run these commands from the
cloned `Mpeg7Dupes` directory. You need Python 3.11 or newer, plus `ffmpeg`,
`ffprobe` and `mpeg7dupes` on PATH. The examples use the shipped
`tools/find_reuse.toml`; if you have edited it, your settings apply instead.
Replace the quoted video paths with your own. If you use uv, replace
`python3` with `uv run`.

Create a folder for the results once:

```sh
mkdir -p reports
```

### 1. If you want to find where one of your clips appears in other videos

Put the videos to search in `downloads/`, including any subfolders. Set
`--source` to your original clip and `--candidates` to that folder, then run:

```sh
python3 tools/find_reuse.py --source "mine.mp4" --candidates "./downloads" \
    --sig-dir "./.signatures" --json "reports/reuse.json"
python3 tools/render_report.py "reports/reuse.json" --out "reports/reuse.html"
```

Open `reports/reuse.html` in your browser. Each row pairs your clip with a
candidate that reached the reporting threshold. Play both videos to check
the shared footage; the players open shortly before the reported match.
With the shipped settings, a hit covers roughly 40% or more of **your source
clip**, not 40% of the candidate. The report shows the matched length and
positions so you can check what was found. `reports/reuse.json` keeps the
complete scan record for later use.

### 2. If you want to check a folder of your clips against another folder

Put your originals in `my_clips/` and the videos to search in `downloads/`.
Keep the two collections in separate folders, then run:

```sh
python3 tools/find_reuse.py --source "./my_clips" --candidates "./downloads" \
    --sig-dir "./.signatures" --json "reports/batch.json"
python3 tools/render_report.py "reports/batch.json" --out "reports/batch.html"
```

Open `reports/batch.html`. Every original is searched for in every candidate;
rows identify which original matched and where. A candidate may appear in
several rows if it contains material from several originals. Originals are
not compared against one another, and paths already in the source collection
are skipped as candidates. For an all-pairs library comparison, use the
[direct signature workflow](#comparing-a-whole-library-directly).

### 3. If you want to check whether one video contains material from another

Use a single file for each side:

```sh
python3 tools/find_reuse.py --source "original.mp4" --candidates "suspected-copy.mp4" \
    --sig-dir "./.signatures" --json "reports/pair.json"
python3 tools/render_report.py "reports/pair.json" --out "reports/pair.html"
```

Open `reports/pair.html` and compare the matched length with both video
lengths. A hit means enough of `original.mp4` was found to reach the threshold;
it does not establish that the two entire videos are identical. Swap the
source and candidate paths to ask the question in the other direction.

### 4. If a scan missed a copy that may have had its top and bottom cropped

Run a separate scan with the optional 5% crop fallback:

```sh
python3 tools/find_reuse.py --source "mine.mp4" --candidates "./downloads" \
    --sig-dir "./.signatures" --no-crop-bars --crop-fallback \
    --json "reports/crop-check.json"
python3 tools/render_report.py "reports/crop-check.json" --out "reports/crop-check.html"
```

Open `reports/crop-check.html` and inspect the rows labelled **Needs review:
5% crop fallback**. These are extra leads found by the fallback within this
scan. Check both players before accepting them: trying cropped views can
also add false matches. This option handles a specific crop hypothesis;
a miss still does not rule out an edited copy.

### What to do with the report

| What you see | What to do |
| --- | --- |
| A match row | Play both sides to confirm the footage and its start/end positions. A row is a lead, not proof that the entire files are duplicates. |
| No candidate reached the threshold | Check that the scan completed. A completed scan found no qualifying match; it does not rule out shorter or unsupported edits. |
| Content or position notes | Inspect the visible details or boundaries as requested. Dark/low-change percentages describe sampled footage, not match confidence. |
| Needs review: 5% crop fallback | Manually verify the extra lead before accepting it. |
| Analysis incomplete | Some visual-property measurements failed. Completed comparisons remain available; inspect the affected videos and the failure reasons. |
| Failed or not compared files | Fix the reported problem and rerun. These files have not been cleared as non-matches. |

The scanner exits with status 0 for a complete run, 1 for partial file or
analysis failure, and 2 for a setup or comparison failure. Check errors before
treating a run as complete; [Reading the result](#does-their-video-contain-my-clip)
explains which partial results remain usable.

To search again, rerun the same command after adding videos or changing the
source path. Keep the entire `.signatures/` directory, including its index:
unchanged signatures and visual analyses are reused. Keep the original video
files available too; the HTML links to them rather than embedding them.

## The recommended flow

**Start with the quick-start commands and keep the defaults for your first
scan.** They separate finding possible reuse from checking the footage:

| Step | What it gives you | Why it matters |
| --- | --- | --- |
| Choose a source and candidates | A search for your clip inside other videos | The reporting threshold is based on how much of **your source** was found. |
| Run `find_reuse.py` | Cached signatures, visual analysis and a JSON scan record | Matching finds possible reuse; analysis adds context for reviewing it. |
| Run `render_report.py` | Side-by-side players and result notes | You can confirm the footage and check its boundaries. Rendering does not repeat the scan. |
| Keep the cache and JSON | Faster reruns and a record you can reopen | Unchanged signatures and visual profiles can be reused. Neither output modifies your videos. |

The sections below explain the choices. Expand the technical details only
when you need to tune, troubleshoot or audit a run.

### Why source and candidate have different roles

**Put the clip you are looking for in `--source`.** The scanner reports a
pair when its matched frame count reaches the configured share of that source:

```text
matched frames / frames in the source >= 0.40
```

With the default 40% threshold:

| Source | Candidate | Shared footage | Result |
| --- | --- | --- | --- |
| 60 seconds | 10 minutes | About 30 seconds | About 50% of the source: passes. |
| 10 minutes | 60 seconds | About 30 seconds | About 5% of the source: does not pass. |

Reversing the files changes the question. Coverage is approximate, especially
when speeds differ; the threshold is a screening rule, not an exact measure
of reused time.

A source folder asks this question for every original against every candidate.
It does not compare originals against one another. To compare every pair in
one library, use the [direct signature workflow](#comparing-a-whole-library-directly).

### Why the quick start keeps the matching defaults

**Use the defaults as a starting point, then change a setting for a specific
problem.** The examples use `tools/find_reuse.toml`; precedence is
**command line → TOML file → built-in defaults**.

| Setting | Why the quick start uses it | When to reconsider it |
| --- | --- | --- |
| `fps = 5` | Measured balance between signature size, time resolution and short-clip detection. | Raise it for short clips. Both sides must use the same rate; the scanner handles this. |
| `min_coverage = 40` | Reports substantial overlap with the source in the measured sets. | Lower it to look for smaller excerpts, accepting more leads to inspect. Short, simple-looking sources can still give false matches. |
| `thxh = 290` | Measured frame-similarity tolerance (`-x 290` in C). | Treat changes as an experiment. Higher values accept more dissimilar frames and can add false matches. |
| `crop_bars = true`, `crop_mode = "motion"` | Removes detected top/bottom bars so the picture can align with an unbordered copy. Uncertain detections leave it uncropped. | Inspect the crop on still or dark footage; see the choices below. |
| `coarse_filter = true` | Saves comparison time and suppresses some short-source false matches. | Disable only for a controlled comparison; it can recover misses and add false matches. |
| `analyze = true` | Explains darkness and low change without changing match decisions. | Use `--no-analysis` to skip the extra analysis work and its notes. |

**Choose cropping by the problem you are investigating:**

| Situation | Setting | What to check |
| --- | --- | --- |
| Ordinary scan | Keep motion bar detection | Check that the reported crop removes bars rather than picture content. |
| Plain black bars on mostly still footage | Try `--crop-mode black` | Dark picture content can also be cropped. This mode is never selected automatically. |
| You want the full picture compared | Use `--no-crop-bars` | Bars remain part of the signature. |
| A copy may have lost 5% at the top and bottom | Use `--no-crop-bars --crop-fallback` | This is an extra crop hypothesis; added hits need review. See [how it works](#optional-fixed-5-crop-fallback). |

These defaults have measured failure cases. See [Limits](#limits) for what to
do about them and [benchmark.md](benchmark.md) for the supporting experiments.

<details>
<summary>Technical details: sampling, coarse filtering and settings validation</summary>

- **Sampling:** a signature contains one fingerprint per sampled frame.
  Frame 300 is ten seconds at 30 fps and sixty seconds at 5 fps; mixing rates
  compares incompatible timelines. Only binary signatures are supported,
  not `format=xml`.
- **Coarse-filter experiment:** `--no-coarse-filter` passes `-d 10001` to C.
  On the tuning corpus, the filter retained every true match and cut about
  two thirds of comparison time. In the first independent validation, turning
  it off recovered 7 of 256 expected matches, all on a static sunset shot
  (5 had been lost through a bar-detector fault fixed since). It also added
  204 false matches: 10/20-second clips and an 8-second opening appeared in
  unrelated videos at 40–100% coverage. Comparison took roughly three times
  as long; fingerprinting was unaffected. The choice is recorded in
  `settings.coarse_filter` and shown in HTML.
- **Validation:** fps must be finite and positive; coverage must be 0–100;
  jobs must be a nonnegative integer; C options must fit their integer ranges.
  TOML booleans must be booleans. Unknown keys, malformed TOML and a missing
  or unreadable explicit `--config` are errors. Settings are checked before
  output is created. Relative executable paths are resolved before any
  subprocess changes directory.

</details>

### Why save JSON, render HTML and keep the cache

**Keep all three: the JSON record, the HTML report and the cache.** They have
different jobs:

| Item | What it is for | What to keep in mind |
| --- | --- | --- |
| JSON from `--json` | Inputs, settings, completed comparisons, matches and failures | You can render another report from it without scanning again. |
| HTML report | Playing and checking matches | It links to the original videos; it does not embed or copy them. Keep those files available. |
| Store at `--sig-dir "./.signatures"` | Reusing signatures and visual profiles | Keep the index and cached files together. The index alone is not enough. |

**For an ordinary rerun, use the same command and cache directory.** New videos
need processing; unchanged ones reuse cached work. Changing fps or crop mode
may need new signatures. Use `--overwrite` when you deliberately want to
regenerate cached work, not for every scan.

<details>
<summary>Technical details: cache identity, rebuilds and concurrent runs</summary>

| Cache behaviour | Detail |
| --- | --- |
| Video identity | Content-based: renamed, moved or copied videos can reuse signatures. The unchanged-file fast path trusts size and mtime and reads no video data. Deliberately restoring both can evade it; `--overwrite` forces reidentification. |
| Signature identity | Includes fps and crop recipe. Exact float fps keys avoid rounded-name collisions; unambiguous integer-fps entries remain usable, while ambiguous old entries are invalidated together. |
| Crop recipes | Motion uses `3`, black uses `black-1`, and fixed fallback uses `fixed5-1`; uncropped signatures have an independent identity. The schema 2 column named `detector` also stores crop recipe versions. A fixed recipe does not mean bars were detected. |
| Visual profiles | The SQLite `profiles` table uses content hash plus analysis recipe, independently of signature fps/crop. Raw samples live in cached JSON; scan records carry summaries, intervals and matched-span measurements. |
| Shared store | Tools must share `--db` and `--sig-dir`, with matching signature settings. Index paths are relative to the signature directory. Files alone cannot reconstruct stored crop/duration metadata. |
| Rebuilds | Signatures are validated under a temporary name, then published as immutable `.gen-<id>.sig` files. Profiles also use immutable generations and checksums. A short transaction switches the index; a failed overwrite preserves the previous generation. Active readers keep their original file. |
| Old generations | Rebuilds can leave unreferenced files; there is no automatic garbage collection. Select signatures through the index, rather than globbing every generation for comparison. |
| Concurrent producers | Per-key file locks and a brief initialization lock protect publication. No SQLite write transaction is held during ffmpeg. Locks live in `.<index-name>.locks`; leave them in place while producers run. This assumes a shared index/directory on one local filesystem, not cross-machine/network locking. Each scan owns its comparison lists. |
| Changing inputs | Size, timestamps and file identity are checked from hashing through decoding; a detected change discards that attempt. |
| Version changes | `--overwrite` regenerates signatures and enabled profiles. FFmpeg upgrades alone do not invalidate them; relevant recipe changes do. |

Detector version 3 selects the first video track consistently with signature
extraction, accounts for display rotation and fails a file when a sampling
window fails. Its motion thresholds are unchanged, but its new identity
rebuilds older cropped signatures once; uncropped signatures are unaffected.
The cheap cache check validates supported header flags and byte counts; the
C loader performs full data validation during comparison.

</details>

### Two-pass visual analysis

**This explains what to inspect in a match; it does not change which videos
match.** Analysis is on by default. Use `--no-analysis` or `analyze = false`
to skip it; `--analyze` overrides that setting.

| Pass | What it checks | Why it helps |
| --- | --- | --- |
| 1. Whole video | Samples darkness, change and contrast throughout the original video | Identifies mostly dark or nearly static footage, including videos with no match. |
| 2. Matched spans | Examines the reported source and candidate spans separately | Bases review notes on the actual match: a dark opening need not describe a bright match later. |

| Note or measurement | What to do |
| --- | --- |
| Dark content | Check that enough visible detail is shared. Darkness alone does not establish underexposure. |
| Low change | Check start/end positions carefully: static or repeating scenes can align at the wrong time. |
| Both properties | Prioritize reviewing both content and positions. |
| Unknown, unavailable or unsupported | Do not read this as “normal.” There may be too little usable analysis to judge. |

Darkness and low change are independent: either, both or neither can apply.
Their percentages describe **sampled time**, not match confidence. An
unflagged match still needs visual review. Analysis leaves signatures, crops,
thresholds, hits, speed ranking and crop-fallback review decisions unchanged.

**Cost:** the first analysis adds a full decode without writing another video.
Later runs reuse profiles without decoding or probing unchanged videos.
An analysis failure preserves completed comparisons and matches, but marks the
run incomplete; see [result statuses](#does-their-video-contain-my-clip).

<details>
<summary>Technical details: sampling rules and colour assumptions</summary>

Pass 1 samples the first video stream at **2 fps**, normalizing SDR luma to
8-bit full range on a **160×90** grid. It measures the whole frame and the
central 80% in each dimension. The centre drives the descriptive flags; the
whole frame supplies border context. Both use the original picture before
any signature crop.

| Property | Descriptive rule |
| --- | --- |
| Dark sample | Mean luma ≤48 and 90th percentile luma ≤80, on the 0–255 scale |
| Low-change interval | Mean absolute luma difference between adjacent samples ≤2, sustained for at least 2 seconds |
| Low contrast | 90th minus 10th percentile luma ≤24 |
| Dominant property | At least 80% of observed time satisfies the rule |

JSON retains ratios, average luma/change, the proportion of pixels below
luma 32 and half-open time intervals. The first sample has no predecessor
and is excluded from the motion denominator. Short changes and small or
periodic movement can be missed. These provisional rules are not calibrated
probabilities or a detector of exact freezes or semantic detail.

Pass 2 includes the last matched signature frame by extending its timestamp
by `1 / fps`. Overruns, insufficient observations and unavailable profiles
remain explicit. HTML shows matched-span notes and time proportions, plus
whole-video summaries and intervals in the inventory even when nothing matched.

| Colour information | How it is handled |
| --- | --- |
| Explicit full/limited range | The tag takes precedence. |
| Missing range | YUV is assumed limited except full-range pixel formats; RGB/gray are assumed full. |
| Missing transfer tag | SDR is assumed, and that assumption is recorded. |
| Tagged HDR or other unsupported transfer | Marked `unsupported`, with no brightness judgement. This acknowledged limitation is not a decode failure. |

Incorrect/missing colour tags, thick borders and content outside the central
region can affect interpretation. Shared decoding with signature extraction
is future work. Measurements are in the
[two-pass development regression](benchmark.md#two-pass-analysis-development-regression).

</details>

### Optional fixed 5% crop fallback

**Use this when a missed copy may have had 5% removed from its top and bottom.**
It tests a specific crop hypothesis, rather than detecting bars or arbitrary
spatial edits.

```sh
uv run tools/find_reuse.py --source mine.mp4 --candidates ./downloads \
    --no-crop-bars --crop-fallback --json reuse.json
uv run tools/render_report.py reuse.json --out report.html
```

The scan first compares full frames. Each pair below the reporting threshold
gets two retries:

| Comparison | Source view | Candidate view |
| --- | --- | --- |
| Baseline | Full frame | Full frame |
| Retry 1 | Crop 5% from top and bottom | Full frame |
| Retry 2 | Full frame | Crop 5% from top and bottom |

For example, the 1000×540 centre of a 1000×600 original may align with the
full frame of its cropped copy. Cropping both sides again would lose that
alignment. `--no-crop-bars` is required to establish the full-frame baseline.

**How to use the result:** manually inspect added hits labelled **Needs review:
5% crop fallback**. More views can produce false matches; these results should
not trigger automatic duplicate deletion. Matching thresholds and speed rules
stay the same. General reframing and arbitrary crops remain unsupported.

**Cost:** each retried pair adds two comparisons. Only videos used in those
pairs need an extra signature and decode; subsequent scans reuse both views.
FFmpeg applies the crop filter directly before `fps,signature`, so no cropped
video is saved or re-encoded.

<details>
<summary>Technical details: crop rounding, evidence and partial failures</summary>

- Each edge rounds to the nearest even pixel, half upwards, using displayed
  height after autorotation. The actual filter is recorded. Signature views
  are extracted separately, not in a shared decode.
- Added hits carry `requires_review: true`; candidates with only those hits
  have status `needs_review`.
- `view_evidence` retains both directional measurements and signature
  identities. The longest result is displayed, source-cropped first on ties.
- `fallback.pairs` retains the full-frame measurement and completion of both
  retries, including comparisons with no CSV match. Each video's extra view
  is under `crop_fallback`: `crop_state: fixed`, `crop_mode: fixed5`, signature
  view `crop5`. Baseline views are `full`, with bar cropping `disabled`.
- Preparation/comparison failures exit 2 and preserve earlier completed hits.
  Unchecked pairs do not become misses. `comparison.sources_completed` covers
  the requested process; `full_frame_sources_completed` separately records
  the baseline. The fallback audit covers usable signatures; the top-level
  summary also accounts for preparation failures.

See [benchmark.md](benchmark.md) for the development measurements.

</details>

### Comparing a whole library directly

**Use this lower-level workflow to compare every pair in one library.** It
produces CSV and requires post-processing; passing one folder as both source
and candidates to the reuse scanner does not do the same job.

1. **Generate one binary signature per video**, with distinct output names
   and the same sampling rate and crop policy. For an uncropped video:

   ```sh
   mkdir -p sig
   ffmpeg -nostdin -i input.mkv -vf "fps=5,signature=filename=sig/input.bin" \
       -map 0:v:0 -an -f null -
   ```

   Repeat for each video. If bars need removing, `tools/detect_bars.py` can
   supply a crop to apply before `fps,signature`; this raw command does not
   detect or remove them automatically.

2. **Compare the generated signatures:**

   ```sh
   find sig -name '*.bin' | sort > siglist.txt
   mpeg7dupes -l siglist.txt > dupes.csv 2> run.log
   ```

   `dupes.csv` contains measured matches; `run.log` records progress and errors.
   Every pair is compared: 600 signatures mean 179,700 pairs.

3. **Screen pairs using the shorter file, then review the footage:**

   ```text
   matched frames / min(frames in file A, frames in file B) >= 0.40
   ```

   Read frame counts from the signature headers. C does not include them in
   its CSV or apply this coverage rule. Passing the rule identifies a pair
   to inspect, not proof that the whole files are duplicates. See the
   [CSV reading guide](#are-these-two-videos-the-same).

The C defaults include `-f csv -m longest -x 290 -i 0 -k 1 -b 0.5`. The reuse
scanner uses `-b 0.1` for its source-excerpt question.

## Reading the result

**Read the HTML report in this order: completion → matches → review notes.**
A match is a lead to check; a completed scan with no hit does not rule out reuse.

| Check | What to look for | Next action |
| --- | --- | --- |
| 1. Did the run finish? | Errors, failed/not-compared files, “Analysis incomplete” | Identify what is missing before interpreting an empty result. |
| 2. Is the same footage visible? | Play **My video** and **Their video** for each row | Confirm content before accepting the match. |
| 3. Are the position and length plausible? | **Reuse starts**, matched length, speed/overrun notes | Scrub around both boundaries; reported times can be wrong. |
| 4. Does this need extra care? | Content/position notes and **Needs review: 5% crop fallback** | Follow the notes; percentages from visual analysis are not confidence scores. |

Use the guide below for the quick-start JSON/HTML workflow. If you ran the C
program directly, skip to [reading its CSV](#are-these-two-videos-the-same).

### Does their video contain my clip

#### 1. Check completion and file statuses first

**Failed or unfinished work is not a negative result.** Every requested source
and candidate stays in the JSON, including failed files and skipped candidates.

| Candidate status | Meaning | What to do |
| --- | --- | --- |
| `matched` | At least one hit reached the threshold without the 5% crop fallback. | Review each hit. Check completion separately: other source comparisons may still be missing. |
| `needs_review` | Its only qualifying hits came from the 5% crop fallback. | Verify both players before accepting a hit. |
| `checked` | Compared against every requested source; no hit reached the threshold. | Read this as “no qualifying match found,” not “no reuse.” |
| `failed` | File processing failed. | Fix the reported cause and rerun. |
| `skipped` | The candidate path is also a source path. | It was intentionally excluded from candidate comparison. |
| `not_compared` | The requested comparisons were not completed. | Resolve the failure and rerun before drawing a conclusion. |

**The scanner's exit code describes the run, not whether it found a match:**

| Exit | Meaning | What remains usable |
| --- | --- | --- |
| `0` | Requested scan completed, with or without hits. | All recorded comparisons; review their results. |
| `1` | Some file processing or enabled visual analysis failed, but usable comparisons completed. | Completed comparisons and hits. Inspect the affected files and rerun as needed. |
| `2` | Invalid settings/tools, no usable comparison, or a comparison failure. | Earlier completed hits, if any. Inspect the error; do not treat this as a complete scan. |

An **Analysis incomplete** warning concerns visual-property measurements;
matching may still have completed. Conversely, a match row can survive a
later comparison failure. Check the run's warnings as well as its rows.

<details>
<summary>JSON details: completion, partial results and report versions</summary>

| Field | Meaning |
| --- | --- |
| `summary.complete` | Whether the overall requested run completed. |
| `comparison.complete` | Comparison completion, independent of visual analysis. |
| `analysis.complete` | Completion of enabled analysis. Operational failures set this and `summary.complete` false; exit 1 unless preparation/comparison requires exit 2. |
| Candidate `comparison_complete` | False if it was not compared against every requested source, even if some comparisons found hits. |
| `comparison.sources_completed` | Sources whose requested comparison process completed. |
| `summary.matches` / `review_matches` | All reported hits / the crop-fallback subset. |

A candidate with both normal and fallback hits is `matched`; each pair keeps
its own `requires_review` flag. Unfinished comparisons never become misses.

Once the input inventory is collected, fatal errors also produce JSON when
`--json` is supplied. Replacement is atomic: a write failure retains the
previous file and exits 2. Early settings errors can also leave an older file
in place, so check errors and the report's generation time after a failed run.
Records use `find_reuse/8`; the renderer accepts versions 3–8 and displays
unfinished work. Missing legacy analysis is labelled “not recorded.”

</details>

#### 2. Confirm the footage, then check the positions

**A threshold-passing match does not establish that the entire videos are
identical.** Compare the visible content and matched length with both video
lengths.

| Report item | How to read it | What to verify |
| --- | --- | --- |
| **Reuse starts** | The first candidate frame the comparison accepted. The source span is recorded too. | Scrub before and after both reported boundaries. |
| **Matched** | Approximate matched length, derived from the matched frame count. | It is not an exact measurement of editing cuts or reused duration. |
| Speed ratio other than `1.0` | The comparison voted for different playback speeds. Coverage counts the slower clip's frames. | Both timing and length need extra care, especially the source-side span. |
| Overrun | A reported span extends beyond a video's end. | Its length and position are unreliable; inspect the apparent shared content manually. |
| Low-change or repeated scenes | Similar frames may occur at many positions. | A correct content match can still be placed a minute or more from its real location. |

The scanner uses **the source's frame count** for the threshold. The terminal
and HTML show that it passed, without presenting coverage as an exact reuse
percentage. JSON retains the measured value for inspection. Short sources
with simple layouts can pass even against unrelated footage; see [Limits](#limits).

<details>
<summary>Measured examples: how approximate are coverage and timestamps?</summary>

- **Coverage can run high:** clips actually using 86%, 50% and 30% of a source
  were measured as 88%, 54% and 33%, because the match walk extended past the
  shared footage. The existing measurements suggest a 40% threshold can fire
  at around 36% actual use; this is not a universal conversion rule.
- **Simple synthetic cuts at ratio 1.0:** both starts were exact in three of
  four cases, and three frames early in the fourth. The smoke test requires
  a copy cut at five seconds to land within one second of that point.
- **These checks are not a timing guarantee:** the reported boundaries belong
  to the comparison walk, not the editor's cuts. Speed changes, low motion
  and repetition can produce much larger errors.

</details>

#### 3. Use review notes to decide what needs a closer look

| Note | Meaning | What to do |
| --- | --- | --- |
| **Needs review: 5% crop fallback** | This hit required an extra cropped view. JSON: `requires_review: true`. | Verify shared content on both sides; the extra views can add false matches. |
| **Content** notes / dark proportion | Describes visible detail in the sampled match span. | Check that the videos share identifiable details, not just a similar light/dark layout. |
| **Position** notes / low-change proportion | The matched scene changes little or has limited evidence for its timing. | Check the start and end manually. |
| **Visual review recommended** | The span assessment recommends closer inspection. JSON: `assessment.review_recommended`. | Follow the content/position notes; this flag does not change the match or candidate status. |
| Analysis unknown, unsupported or not recorded | No usable judgement is available for that measurement. | Inspect manually; absence of a flag does not verify the match. |

**The two review flags serve different purposes:** `requires_review` marks
crop-fallback hits; `assessment.review_recommended` adds visual-analysis
advice. Only the former contributes to the crop-fallback `needs_review` status.
Dark/low-change percentages measure sampled time, not probability of a match.

Crop decisions also appear for each processed video, on fresh and cached runs:

| Crop decision | Meaning | What to check |
| --- | --- | --- |
| `disabled` | Automatic bar cropping was off. | The baseline used the full frame. |
| `detected` | The selected detector supplied a crop. | It should remove bars, not picture content. |
| `none` | No bar crop was selected. | This does not prove bars are absent. |
| `uncertain` | Detection could not decide confidently; the picture was kept. | A barred copy may be missed. |
| `fixed` | An extra view deliberately removed 5% from the top and 5% from the bottom. | This is the fallback hypothesis, not a detected bar boundary. |
| `unknown` | Legacy crop metadata is missing. | The record cannot establish the crop decision. |

#### 4. If the report's players are blank

**The HTML links to videos on disk; it does not contain them.** Keep the video
files at their recorded locations. To render the same scan into another folder:

```sh
mkdir -p reports
uv run tools/render_report.py reuse.json --out reports/reuse.html
```

| Situation | What to do |
| --- | --- |
| Only the HTML output folder changed | Render again to the desired path. The saved `path_base` resolves relative input paths from the original scan folder. |
| An old JSON record has no `path_base` | Add `--path-base /original/scan/folder` when rendering. Without it, the renderer warns and assumes the report folder. |
| The video tree moved and the record uses relative paths | Add `--path-base /new/video/root` when rendering. Keep the same layout within that root. |
| The videos moved and the record uses absolute paths | Restore their recorded locations or scan their new locations. `--path-base` does not relocate absolute paths. |

<details>
<summary>JSON reference: settings, input identities and analysis evidence</summary>

Use these fields to check how a result was produced:

| Field | What it records |
| --- | --- |
| `settings.comparison_args` | Actual C flags, including the scanner's `-b 0.1 -d 9000 -c 60000` with the default coarse filter. |
| `settings.crop_mode`, video `crop_mode`, `tool.detector.mode` | Selected `motion`, `black` or `disabled` bar-crop mode. Extra fixed views and their evidence are described under [crop fallback](#optional-fixed-5-crop-fallback). |
| `settings.analyze`, `tool.analysis` | Whether analysis was enabled, plus current analysis code and recipe. |
| `tool` | Binary SHA-256 and scanner, detector and signature-producer code identities. |
| Video `content_hash` | BLAKE2b-128 identity of the video content. |
| Video `signature` | Filename, SHA-256, stored detector version and original ffmpeg version. Cache hits retain the original producer version; missing legacy identities stay unknown. |
| Video `analysis` | Status, recipe, original FFmpeg version, summaries, intervals and cached artifact SHA-256. |
| Hit `assessment` | Source/candidate span measurements, separate content/position notes and `review_recommended`. |
| Hit view evidence | Exact source/candidate view, crop and signature used; fallback retains both directional measurements. |
| `jobs_requested` | Requested comparison workers; 0 means automatic. C logs the effective count after capping it to available cores. |
| `path_base` | Resolution base for the original input paths. The record keeps those supplied paths; the renderer makes links relative to the HTML. |

Recording SHA-256 reads signatures even on cache hits; it does not decode or
rehash unchanged videos. Older motion/disabled records remain readable.

</details>

### Are these two videos the same

**For direct C output, use coverage and visible content to screen duplicate
candidates. Do not use `score` or `whole` as proof.** The CSV has one row per
pair with a reported match; zero-score pairs are omitted. A missing row alone
does not tell you whether the pair was compared—check the run's completion.

| Read first | Meaning | How to use it |
| --- | --- | --- |
| `matchframes` and both signature frame counts | Matched frames relative to each file's length | For the library workflow, screen with `matchframes / min(frames A, frames B) >= 0.40`. Read the denominators from signature headers; C does not apply this rule. |
| `begin` / `end` for each file | First and last accepted matched frames; `end` is inclusive | Find the shared footage and inspect both boundaries. |
| `framerateratio` | Voted speed of the second clip relative to the first | Values other than 1.0 make coverage and positions less reliable. |
| `whole` | The walk reached a beginning and an end, possibly in different files | A shared opening/ending can set it to 1 even when the rest is unrelated. |

**Example: a resized copy with five seconds trimmed from each end.** Both
signatures were sampled at 5 fps; the original was 320×180 and the copy 240×136.

| Value | Interpretation |
| --- | --- |
| `matchframes = 550`; shorter signature = 550 frames | The shorter clip matches end to end. |
| `begin 1 = 5.00`, `end 1 = 114.80` | The match starts five seconds into the original. |
| `begin 2 = 0.00`, `end 2 = 109.80` | It starts at the beginning of the trimmed copy. |
| `framerateratio = 1.0` | No speed difference was voted for this match. |

**Counterexample:** two unrelated benchmark clips sharing a 70-second ad had
`whole = 1`, 351 matched frames and a score higher than any genuine duplicate.
The shared ad was real; treating the entire videos as duplicates would be wrong.

<details>
<summary>CSV reference: all numeric columns and the raw example</summary>

The first two fields name the signature files. The remaining columns are:

| Column | Meaning |
| --- | --- |
| `score` | Votes for the winning alignment, measured from whichever file is first. Not a length or confidence score. |
| `matchframes` | Frames of the second clip covered by the walk, including up to three tolerated bad frames at each end. At a speed ratio other than 1.0 it counts the slower clip's frames. |
| `goodframes`, `totalframes` | Numerator and denominator of the `-b` test; a stricter `-b` can be applied to a finished CSV. |
| `offset` | Seed pair position inside its Hough window. A diagnostic, not the shift between the files. |
| `framerateratio` | Voted speed of the second clip relative to the first, on a grid of thirtieths. |
| `meandist` | Mean frame distance over the match; lower is closer. |
| `time 1 [s]`, `time 2 [s]` | Seed frame times inside the match, not its endpoints. |
| `begin 1 [s]`, `end 1 [s]`, `begin 2 [s]`, `end 2 [s]` | First and last frames accepted in each file, expressed in seconds at the sampling rate. |
| `whole` | 1 when the walk reached a beginning and an end; they need not belong to the same file. |

The example above is this raw row:

```csv
original.bin,reupload.bin,1056,550,550,550,-26,1.000000,12.57,6.00,1.00,5.00,114.80,0.00,109.80,1
```

Comparison runs across cores and rows arrive as they finish; do not assume
fixed row order or which file appears first. `score` and `offset` depend on
orientation. Interpret each file's boundaries using its filename, not row
position; the matched count and per-file boundaries are unchanged by orientation.

Paths containing commas, quotes or line breaks are CSV-quoted, with inner
quotes doubled. Use a CSV parser. CSV is the only C output; the old `beautiful`
tree was removed in build 8. `render_report.py` provides the readable view for
scanner JSON, not raw C CSV.

</details>

## Limits

Each item explains the problem, the observed conditions, and what you can do.
The figures come from tests or [benchmark.md](benchmark.md). The short-clip
synthetic measurements below were checked with build 5; later validation
results are identified separately. These are observations on those materials,
not guaranteed boundaries for every video.

### 1. Missed matches: short clips are missed silently

A very short excerpt can be absent from the results without an error or a
warning that this particular match was missed.

**Why this happens and what was measured**

The comparison needs enough sampled frames to establish a match. At **5 fps**,
the synthetic tests found:

| Comparison | Sampled length | Observed result |
| --- | --- | --- |
| A clip against its identical or half-width re-encoded copy | From 10 frames, about 2 s | Matched |
| An excerpt inside a longer video | From 50 frames, about 10 s | Matched end to end |
| An excerpt inside a longer video | 15–40 frames, about 3–8 s | Only part of the excerpt matched |
| An excerpt inside a longer video | Fewer than 15 frames, under 3 s | No match reported |

Real footage with less visual detail can require a longer excerpt. These
measurements do not establish a precise cutoff for every length or scene.

**What you can do**

- Try a higher `fps` when searching for short excerpts, and regenerate both
  source and candidate signatures at that same rate.
- Check missed excerpts manually. Raising `fps` is not a guaranteed fix for
  footage with little visual detail; see item 3.

### 2. False matches: short sources can appear to match unrelated videos

A reported match can be wrong when a short source and an unrelated candidate
have similar light/dark layouts.

**Why this happens**

- The reuse scanner reports a match at 40% of the **source's** frame count.
- At `-x 290`, frames with a similar layout, such as a dark sky above a lit
  foreground, can be accepted even when their content is unrelated.
- An unrelated video can supply roughly 40 seconds of sufficiently similar
  frames. That is enough to pass the threshold for a short source.

**What was measured**

A length test on the second validation set used two night timelapses and a
basketball video game. For sources with these simple layouts:

| Source length | Unrelated candidates reported as matches |
| --- | --- |
| 10–60 s | About 5–10% |
| 90 s | About 1.5%, with reported coverage of 40–45% |
| 120 s and 5 min | None in this test |

Other source material produced one false match across the tested lengths.
The absence of false matches at 120 seconds in this test is not a safe-length
guarantee.

**What you can do**

- Review short-source matches in the HTML report, especially when the picture
  has a simple layout.
- Do not use `meandist` as an automatic fix. These false matches tended to have
  higher distances, but every tested cutoff that removed them also lost real
  reframed or differently cropped copies. No such filter is applied.

### 3. Missed matches: nearly featureless footage can fail to match even itself

A genuine copy may produce no result when the picture contains too little
visual detail.

**Why this happens and what was measured**

Lack of detail and lack of motion are different problems:

| Tested footage | Observed result |
| --- | --- |
| Pairs of different synthetic solid-colour or test-pattern clips | No rows at the defaults; a solid colour also failed against its own half-width re-encode |
| Static but textured colour bars | Matched a letterboxed copy end to end |
| Real locked-off sunset and night sky, first validation set | Their copies were recognised |
| A 10 s excerpt of that sunset inside other footage | Missed |
| A talk show at a dinner table | Behaved like the other ordinary footage |

The static synthetic cases did not produce false matches in that test;
this does not establish that all static footage is safe. Low-motion footage
can also have the position errors described in item 4.

**What you can do**

- Treat a miss on nearly featureless footage as inconclusive.
- Inspect the footage manually. More samples cannot create visual detail that
  the picture does not contain; there is no validated universal setting fix.

### 4. Position errors: a correct match can still have wrong start and end times

Finding the right pair of videos does not guarantee that the reported segment
is at the right place.

**Why this happens**

- Still shots, loops and repeated views can look alike at different moments.
- The comparison can align those moments incorrectly, especially when the
  copy is also sped up or reframed.
- Another moment of the same still shot can be indistinguishable from the
  exact excerpt you are searching for.

**What was measured**

Examples from the first and second validation sets:

| Material and edit | Reported position error |
| --- | --- |
| Slide talk against its 1.25x copy | 18–30 s |
| 10 s basketball video-game excerpt inside a 1.25x copy | 107 s |
| 20 s night-timelapse excerpt inside a vertical reframe | 81 s |
| Locked-off sunset | Up to 90 s |
| Sunset excerpt matched to another moment of the same still shot | 50 s from its actual cut position |

The first four examples were true matches with incorrect positions. They do
not imply that every match on repetitive footage is genuine; false matches
are a separate risk, as described in item 2.

**What you can do**

- Use the timestamps as starting points for inspection, not exact edit points.
- Play both videos and verify the beginning and end of the shared material.
- Check low-change/position notes when available. A ratio of `1.0` does not
  guarantee correct alignment.

### 5. Duplicate ambiguity: shared openings can pass the coverage threshold

Two different videos can qualify as duplicate candidates because they share
an opening, advertisement or another sufficiently long segment.

**Why this happens**

Coverage measures **how much is shared**, not **what is shared**. For the
same-video question, the rule uses the longest shared stretch divided by the
shorter file's frame count. Separate openings and closings are not added up.

**What was measured**

| Shared material and video length | Coverage / outcome |
| --- | --- |
| Same 8 s opening on two otherwise unrelated 60 s clips | 43 matched frames, about 14%; below the 40% rule |
| Same opening on two 20 s clips | About 43%; passes the rule |
| Five-minute cuts of two series episodes, sharing a 90 s title sequence and the same inserted advertisement | 37.8%; below the rule |
| Whole episodes of that series, 25–30 min long | About 5–6% shared |

At the 40% threshold, a shared stretch reaches the rule when the shorter
video is no more than about 2.5 times as long as that stretch. In the
benchmark's trimmed-video construction, advertisement-only pairs and the
weakest true-copy pairs both reached 40% when the shared advertisement was
two minutes long. Longer advertisements reversed that separation.

**What you can do**

- Inspect the boundary columns or HTML players to see whether the match is
  only an opening, closing or advertisement.
- Check the programme content before treating the files as duplicates.
  Coverage alone cannot distinguish these cases.

### 6. Speed changes: timestamps and coverage can be distorted

A sped-up or slowed-down copy can be found while its reported position or
source coverage remains inaccurate.

**Why this happens**

- Speed ratios use a grid of thirtieths. A true ratio between grid points
  causes alignment drift; the error grows with the match's length and the
  gap between the true and selected ratios.
- `matchframes` counts frames of the slower clip. The reuse scanner still
  divides that count by the source's frame count, so its coverage is not
  adjusted to the actual amount of source footage used at another speed.

**What was measured**

For a 60 s synthetic source, build 7 found both the 1.25x and 0.8x copies
whole, with 240 and 300 matched frames respectively. Two limitations remained:

| Case | Observed error |
| --- | --- |
| 0.8x copy | Start reported at 2.6 s instead of 0 because of the ratio grid |
| 1.25x copy containing the whole source | Reuse-scanner coverage read 80% |

Historical fixes should not be confused with these remaining limits: build 6
lost the faster copy within a few frames and voted `0.07` for a 12 s quotation
because its candidate scan covered only half the accumulator. That quotation
now votes `1.0` with the correct source span.

**What you can do**

- Verify both boundaries when the reported speed ratio differs from `1.0`.
- Do not interpret the reported coverage as an exact proportion of source
  content at another speed. Speed-adjusted source coverage remains separate
  planned work; changing the reporting threshold does not correct the measure.

### 7. Speed detection: a short excerpt in a sped-up copy can report ratio 1.0

The tool can find a real excerpt but fail to identify that the containing
video has been sped up.

**Why this happens**

The comparison keeps the longest candidate alignment. On slowly changing
footage, an incorrect normal-speed alignment can remain plausible for longer:

| Alignment of a 10 s excerpt against a 1.25x copy | Walk behaviour |
| --- | --- |
| Reported ratio `1.0` | 50 steps; drifts about 2 s out of alignment |
| Correct ratio `0.80` for this comparison direction | 40 steps; stays aligned |

If both walks survive, the longer, incorrect one wins. This affected still
shots, loops, timelapses, slides, a video game and one news clip whose frames
remained similar two seconds apart.

**What was measured**

- On the first two validation sets, whole-source 1.25x copies were voted at
  `0.80` and placed exactly.
- All 29 tested 10/20 s excerpts inside such copies were found, but 13 reported
  ratio `1.0`.
- Typical start errors reached one fifth of the excerpt: 2 s for a 10 s clip
  or 4 s for a 20 s clip. The output omitted the speed change and reported
  100% coverage instead of 80%.
- Two repetitive-footage cases were placed much farther away, as in item 4.

**What you can do**

- Check playback and boundaries even when the ratio says `1.0`.
- Allow for short-excerpt timing errors when reviewing a sped-up copy.
  Candidate ranking is intentionally unchanged; there is no automatic
  correction for this case.

### 8. Coverage limits: only the longest match is reported for each pair

Repeated uses of one source appear once, and footage shared in several
separate pieces can fail the coverage threshold.

**Why this happens and what was measured**

- A source inserted in three places produces one result: the longest match.
  Listing every use is outside the tool's intended scope.
- Different insertions in two copies can split their shared body into pieces.
  The pieces are not summed; one piece must reach 40% of the shorter file to
  pass the same-video rule.
- On the first two validation sets, 8 of 11 such pairs were missed. Their
  longest shared piece was 38.7% of the shorter file.

**What you can do**

- Inspect other parts of a matched candidate if you need every occurrence.
- Treat a miss as inconclusive when the footage has been rearranged or
  interrupted by insertions. This program does not provide a combined
  coverage total across separate matches.

### 9. Cropping errors: still picture edges can be confused with bars

The default motion detector may leave real bars in place, or remove still
picture content that resembles a bar.

**Why this happens**

- With almost no motion, the detector cannot distinguish bars from picture;
  it reports uncertainty and leaves the video uncropped.
- A still edge in otherwise moving footage can look like a bar.
- A brief moving insert may not resolve the ambiguity if its edges look the
  same as the surrounding still shot in every sampled window.

**What was measured**

| Detector / material | Observation |
| --- | --- |
| Version 1, first validation set | An unbarred sunset with a 10 s insert lost 482 of 1080 rows; two other unbarred files lost 274 and 136 rows |
| Version 2 | Requiring consistent bar appearance across sampled windows fixed those cases without changing the 96-video tuning-set results |
| A still city skyline below a moving night sky | 103 picture rows were mistaken for a bar |
| Version 2, second validation set | No incorrect crops among 116 copies in that set |
| Motion 3, third validation set | An unbarred short landscape was over-cropped; the older clean result was not a general guarantee |

**What you can do**

- Inspect the recorded crop when the picture has still edges or very little
  motion.
- Use `--no-crop-bars` to compare the full frames when automatic cropping
  removes real picture content. This also leaves genuine bars in place.
- For plain black bars on still footage, consider the explicit black mode
  described in item 10 and check its crop too.

### 10. Missed matches: added bars can hide a mostly static slide presentation

A copy with top/bottom bars can be missed completely when the default detector
cannot identify the bars.

**Why this happens**

- A nearly static centre gives the motion detector too little evidence to
  distinguish the picture from its bars, so it crops nothing.
- The added bars change the picture's position within the frame, making the
  uncropped signatures harder to align.

**What was measured**

| Material | Observation |
| --- | --- |
| Second validation set: slide talk with bars added | Its two barred copies matched none of its ten other copies in either question |
| The same copy after manually cropping the bars | Matched the original on 1500 of 1500 frames |
| First validation set: sunset and night sky | Their barred copies were found; this failure does not affect every still video |
| Third validation set: motion 3 and black-1 | Black recovered some plain barred still copies, but over-cropped dark dock picture; lettered copies still failed |

The archived slide-talk original has not been retested with black mode.

**What you can do**

- Explicitly try `--crop-mode black` in `find_reuse.py`, or set
  `crop_mode = "black"` in its TOML. The standalone detector uses `--mode black`.
  There is no automatic switch from motion to black.
- Review the proposed crop before trusting either a match or a miss. Dark
  picture edges can be removed, lettering can prevent the crop, and changes
  outside the sampled windows can be missed.
- Use `--no-crop-bars` to disable either detector. Both sides use the chosen
  mode; separate cache keys let you switch back to the existing motion
  signatures.

**What the black-mode workaround checks**

- ffmpeg `cropdetect` examines full-resolution, full-range grey with a black
  threshold of 16/255 and no bright outliers.
- It keeps the union of picture bounds across the sampled windows, rounds
  toward retaining picture, and crops only the top and bottom.
- All-dark or excessively narrow picture is uncertain and stays uncropped.
- Generated tests cover near-black compressed bars and a still textured copy,
  including cold/hot scans. These tests do not make the detector universally
  correct; see the [third-set results](benchmark.md#third-independent-validation-2026-09-16).

### 11. Lost content: an unchanging full-width band can be cropped away

A news ticker or caption strip may be removed from the comparison together
with the bars.

**Why this happens and what was measured**

- The motion detector looks for rows that do not change. A band spanning the
  full width and staying unchanged throughout the video satisfies that rule.
- On the second validation set, a 119-row ticker was removed from every copy
  that carried it whole. Matching still worked because those copies were
  cropped consistently, but the ticker's content was no longer compared.
- A scoreboard covering only part of the width was retained because the rest
  of those rows moved.

**What you can do**

- Inspect the crop if captions or overlays are important to your comparison.
- Use `--no-crop-bars` when you need that content retained, and check how any
  remaining bars affect the result.

### 12. Unsupported transformations: vertical reframes and side bars remain difficult

A missed match cannot rule out a copy that has been substantially reframed.

**Why this happens**

- A common 16:9-to-9:16 reframe places the original picture across the middle
  of a blurred enlargement. The blurred background fills much of the frame
  and is compared as picture; the tool does not undo this layout.
- The bar detectors only inspect the top and bottom. Left/right bars, such
  as pillarboxing a 4:3 picture into 16:9, are not detected.

**What was measured**

| Blurred vertical reframes in the second validation set | Matches found |
| --- | --- |
| Same-video pairs | 63 of 106 |
| Contains-my-clip pairs | 14 of 27 |
| Slide talk, letterboxed source or source carrying a ticker | None |

Other transformations, such as cropping the picture directly to 9:16, were
not tested in that set. Individual portrait, rotation and pillarbox successes
in the third set do not establish general reframe support.

**What you can do**

- Review suspected reframes manually even when no match is reported.
- Do not treat black mode or the fixed 5% top/bottom fallback as a general
  solution for vertical layouts or side bars.

### 13. Validation scope: benchmark results are not a general accuracy guarantee

A setting that works on a validation set can still fail on your footage.

**What the evidence covers**

The first two independent sets used sixteen sources not in the original
tuning set. Their historical results were:

| Set | Same-video pairs found | False matches among other pairs | Contains-my-clip pairs found | False matches among other pairs |
| --- | --- | --- | --- | --- |
| First: 6 sources, 72 files, 12 edit types | 382/396 | 0/2160 | 249/256 | 3/1184 |
| Second: 10 sources, 116 files, settings frozen with detector 2 | 575/643 | 0/6027 | 350/369 | 73/2995 |

- The first set included a talk show, animation, sports, a star field, a
  sunset and a night sky. Of its 24 errors, 21 involved the low-motion sunset
  and 17 involved cropping errors; these groups overlap.
- After detector 2 was fixed using that first set, its results improved to
  394/396 same-video pairs and 254/256 contains-my-clip pairs, with two false
  contains-my-clip matches. That rerun is development evidence, not an
  independent validation.
- The second set's misses included vertical reframes, barred still slides
  and shared footage split by insertions. Its 73 false matches came from
  sources of 20 s or less; the later length test found risks up to about two
  minutes.
- The [third independent set](benchmark.md#third-independent-validation-2026-09-16)
  separately measured motion 3 and black-1 and found further crop, match and
  position errors. Its footage was later reused to develop crop fallback
  and visual analysis; those later runs are development regressions.

**What you can do**

- Read the dataset and version labels with each result in
  [benchmark.md](benchmark.md); do not combine them into one accuracy claim.
- Check representative examples from your own collection, including unrelated
  videos and difficult edits, before relying on the results.
- Treat a clean historical test as evidence for its tested material, not a
  promise that the same failure cannot occur elsewhere.

### 14. Performance limits: cached signatures still require comparison work

Keeping signatures avoids decoding the videos again, but a large library can
still take substantial time to compare.

**Why this happens**

- Candidate signatures are read and parsed again for each pair in which they
  are compared; there is no shared cache of all parsed candidates.
- Single-source comparisons share the loaded source across workers, but
  still load each candidate. Full-library work grows with the number of pairs.

**What you can do**

- Keep the signature cache and its index to avoid repeating video decoding.
- Plan for comparison time as the library grows. For long C comparison runs,
  follow [Long runs](#long-runs) to record progress and resume interrupted work.

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
