# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
sigmake.py
==========

Makes one signature file: probe the duration, decide about bars, run ffmpeg,
check what came out, put it in place, record it. sigstore.py decides whether a
signature may be reused; this is what happens when it may not. The two
producers, tools/find_reuse.py and the root make_signatures.py, used to carry
their own copy of this and drifted: one crashed on a cache hit, both accepted
a truncated file as a signature.

Atomic on disk
--------------
ffmpeg writes to a temporary name in the signature directory, and the file is
renamed into place only after its header has been read back and its size
checked against the counts in it. A failed run leaves nothing behind, and on
--overwrite the previous good signature stays where it was. Written straight
to the final name, an interrupted ffmpeg left a truncated file that was not
empty and so counted as a cache hit.

build() chooses a fresh generation filename, so it never replaces bytes named
by an existing row. make() holds a per-key file lock through lookup, decode and
publication, then switches the index in a short transaction. An interruption
before the commit leaves at worst an unreferenced generation; an old row still
names its old bytes. Old generations stay available to active scans. Source
stat identity must remain unchanged from identification through publication.

Bars: five answers, not two
---------------------------
detect_bars can say bars are here, that there are none, that it cannot tell
(a video too still to read), or fail outright (ffprobe cannot open it). Both
producers used to fold the last three into an empty crop, so a signature taken
from a video that could not be read went into the index as one with no bars.
CropDecision keeps them apart: disabled, detected, none, uncertain, failed. A
failure is the file failing, not a signature with no bars; uncertain keeps an
uncropped signature and says so in the index.

Tools
-----
Every call to ffmpeg and ffprobe, the detector's included, uses the paths the
caller was given. The detector used to take whatever was on PATH, so --ffmpeg
changed which ffmpeg fingerprinted a video and not which one looked for bars.
"""

import os
import math
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import sigstore
from tool_settings import ToolError, run_tool

try:
    import detect_bars
except ImportError:
    detect_bars = None


class SignatureError(RuntimeError):
    """One file could not be fingerprinted; the message says why."""


@dataclass
class CropDecision:
    state: str        # disabled | detected | none | uncertain | failed
    crop: str = ""    # the ffmpeg crop filter, when detected
    detail: str = ""  # why, in a few words, for the log


@dataclass
class Built:
    """What producing one signature found out, before it is recorded."""
    duration: float
    crop: CropDecision
    frames: int
    filename: str


@dataclass
class Signature:
    """One usable signature, whether it was just made or found in the store."""
    path: Path
    filename: str
    content_hash: str
    duration: float
    frames: int
    crop: str
    crop_state: str
    produced: bool
    ffmpeg: str = ""
    detector: str = ""


def resolve_tool(program: str) -> str | None:
    """Where this program is, or None: on PATH, or an explicit path."""
    found = shutil.which(program)
    return str(Path(found).resolve()) if found else None


def probe_duration(ffprobe: str, path: Path) -> float:
    """The container's duration in seconds. Video information, not a frame
    count: the count comes from the signature once it exists."""
    done = run_tool(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        raise SignatureError(f"ffprobe failed: {done.stderr.strip()[:200]}")
    text = done.stdout.strip()
    if not text or text == "N/A":
        raise SignatureError("ffprobe reports no duration")
    try:
        value = float(text)
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except ValueError:
        raise SignatureError(f"ffprobe reports a duration of {text!r}") from None


def decide_crop(path: Path, crop_bars: bool, ffmpeg: str,
                ffprobe: str, *, crop_mode: str = "motion") -> CropDecision:
    """What to crop off this video, and how sure that is.

    The detector's constants are its own, not arguments here: two callers
    passing different values would crop one video two ways while
    DETECTOR_VERSION said they agreed.
    """
    if not crop_bars:
        return CropDecision("disabled")
    if detect_bars is None:
        return CropDecision("failed", detail="detect_bars.py is not importable")
    try:
        found = detect_bars.analyse(
            path, points=detect_bars.POINTS, per_point=detect_bars.PER_POINT,
            threshold=detect_bars.THRESHOLD,
            min_fraction=detect_bars.MIN_FRACTION,
            ffmpeg=ffmpeg, ffprobe=ffprobe, mode=crop_mode)
    except ToolError:
        raise
    except Exception as exc:                      # noqa: BLE001
        return CropDecision("failed", detail=f"bar detection raised {exc}")
    status = found["status"]
    if status == "failed":
        return CropDecision("failed", detail=found.get("reason", "sampling failed"))
    if status == "unreadable":
        return CropDecision("failed", detail="ffprobe cannot read the file")
    if status in ("too short", "too static", "too dark", "ambiguous"):
        return CropDecision("uncertain", detail=status)
    if status != "ok":
        return CropDecision("failed", detail=f"bar detector said {status!r}")
    if not found["bar_fraction"]:
        return CropDecision("none")
    return CropDecision(
        "detected", crop=f"crop=iw:{found['picture_height']}:0:{found['top']}",
        detail=f"{found['top']} rows top and {found['bottom']} bottom "
               f"of {found['height']}")


def produce(src: Path, sig_dir: Path, filename: str, crop: str, fps: float,
            ffmpeg: str, hwaccel: str = "", *, before=None) -> dict:
    """Run ffmpeg and put a checked signature at sig_dir/filename.

    Returns the header read back from the file. Raises SignatureError, with
    the temporary file removed and any previous signature untouched, when
    ffmpeg fails or what it wrote is not a whole signature.
    """
    # Same directory as the final file, so the rename is a rename. A leading
    # dot and a .part suffix keep it out of any listing of *.sig.
    temp = f".{filename}.{os.getpid()}.{secrets.token_hex(4)}.part"
    chain = f"{crop}," if crop else ""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    # Only the filename goes in the filtergraph, with cwd locating the output,
    # because a path in there needs escaping that is easy to get wrong.
    cmd += ["-i", str(src.resolve()), "-map", "0:v:0", "-an",
            "-vf", f"{chain}fps={sigstore.fps_tag(fps)},signature=filename={temp}",
            "-f", "null", "-"]
    try:
        done = run_tool(cmd, cwd=sig_dir, capture_output=True, text=True)
        if done.returncode != 0:
            raise SignatureError(
                f"ffmpeg exited {done.returncode}: {done.stderr.strip()[:200]}")
        header = sigstore.read_header(sig_dir / temp)
        if header is None:
            size = (sig_dir / temp).stat().st_size \
                if (sig_dir / temp).exists() else 0
            raise SignatureError(
                f"ffmpeg exited 0 but wrote no usable signature "
                f"({size} bytes)")
        if before is not None:
            sigstore.check_unchanged(src, before)
        os.replace(sig_dir / temp, sig_dir / filename)
        return header
    finally:
        try:
            (sig_dir / temp).unlink()
        except FileNotFoundError:
            pass


def build(src: Path, sig_dir: Path, *, content_hash: str, fps: float,
          crop_bars: bool, detector: str, ffmpeg: str, ffprobe: str,
          hwaccel: str = "", before=None, crop_mode: str = "motion") -> Built:
    """Everything that does not touch the index, so a caller may run it on a
    worker thread: probe, decide about bars, produce, read the count back."""
    before = before or src.stat()
    sigstore.check_unchanged(src, before)
    duration = probe_duration(ffprobe, src)
    if crop_bars and detect_bars is not None and detector != detect_bars.detector_id(crop_mode):
        raise ValueError("detector identity does not match crop_mode")
    decision = decide_crop(src, crop_bars, ffmpeg, ffprobe, crop_mode=crop_mode)
    if decision.state == "failed":
        raise SignatureError(f"bar detection failed, {decision.detail}")
    filename = sigstore.sig_filename(content_hash, fps, crop_bars, detector)
    # Never replace a file that an index row (or an active scan) may still
    # reference. A failed index commit leaves at worst an unreferenced file.
    filename = filename[:-4] + ".gen-" + secrets.token_hex(16) + ".sig"
    header = produce(src, sig_dir, filename, decision.crop, fps, ffmpeg, hwaccel,
                     before=before)
    return Built(duration, decision, header["frames"], filename)


def record(con, content_hash: str, size: int, built: Built, *, fps: float,
           crop_bars: bool, detector: str, ffmpeg_version: str) -> None:
    """Write the produced metadata inside the caller's short transaction."""
    sigstore.remember_content(con, content_hash, size, built.duration)
    sigstore.remember_signature(
        con, content_hash, fps, crop_bars, detector, built.crop.crop,
        built.frames, ffmpeg_version, built.filename, built.crop.state)


def lookup(con, sig_dir: Path, content_hash: str, *, fps: float,
           crop_bars: bool, detector: str) -> Signature | None:
    """The stored signature for this content under these settings, if the
    store has a usable one and knows the video's duration."""
    found = sigstore.find_signature(con, sig_dir, content_hash, fps,
                                    crop_bars, detector)
    facts = sigstore.content_facts(con, content_hash)
    if not found or not facts:
        return None
    return Signature(found["path"], found["filename"], content_hash,
                     facts["duration"], found["frames"], found["crop"],
                     found["crop_state"], produced=False,
                     ffmpeg=found["ffmpeg"], detector=found["detector"])


def make(src: Path, con, sig_dir: Path, *, fps: float, crop_bars: bool,
         detector: str, ffmpeg: str, ffprobe: str, ffmpeg_version: str,
         hwaccel: str = "", overwrite: bool = False, identified=None,
         crop_mode: str = "motion") -> Signature:
    """The whole flow for one video on the calling thread: identify it, reuse
    the store's signature if there is one, otherwise make and record one.

    Raises OSError (FileChanged included) when the video cannot be read or
    changes underfoot, and SignatureError when it cannot be fingerprinted.
    """
    sigstore.fps_tag(fps)  # Reject unusable cache keys before touching the store.
    if crop_bars and detect_bars is not None and detector != detect_bars.detector_id(crop_mode):
        raise ValueError("detector identity does not match crop_mode")
    if con.in_transaction:
        raise ValueError("make requires a connection without an open transaction")
    if identified is not None:
        content_hash, stat = identified
        sigstore.check_unchanged(src, stat)
    else:
        stat = src.stat()
        content_hash = None if overwrite else sigstore.known_hash(con, src, stat)
        if content_hash is None:
            content_hash, stat = sigstore.identify(src)
    with sigstore.signature_lock(con, content_hash, fps, crop_bars, detector):
        sigstore.check_unchanged(src, stat)
        if not overwrite:
            found = lookup(con, sig_dir, content_hash, fps=fps,
                           crop_bars=crop_bars, detector=detector)
            if found:
                sigstore.remember_file(con, src, content_hash, stat)
                return found
        built = build(src, sig_dir, content_hash=content_hash, fps=fps,
                      crop_bars=crop_bars, detector=detector, ffmpeg=ffmpeg,
                      ffprobe=ffprobe, hwaccel=hwaccel, before=stat, crop_mode=crop_mode)
        try:
            sigstore.check_unchanged(src, stat)
            con.execute("BEGIN IMMEDIATE")
            record(con, content_hash, stat.st_size, built, fps=fps,
                   crop_bars=crop_bars, detector=detector, ffmpeg_version=ffmpeg_version)
            sigstore.remember_file(con, src, content_hash, stat)
            con.commit()
        except BaseException:
            con.rollback()
            (sig_dir / built.filename).unlink(missing_ok=True)
            raise
        return Signature(sig_dir / built.filename, built.filename, content_hash,
                         built.duration, built.frames, built.crop.crop,
                         built.crop.state, produced=True,
                         ffmpeg=ffmpeg_version, detector=detector if crop_bars else "")
