# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
sigstore.py
===========

A signature store: a SQLite index beside a directory of `.sig` files.

Signatures are expensive — on 96 files, generating them took 19 minutes and
comparing them took 45 seconds — so the whole point of keeping them is not
having to make them again. This decides when that is safe.

Three tables, because the counts differ
---------------------------------------
    many paths ──→ one content ──→ many signatures
                                   (per fps, per crop setting)

    /video/a.mp4  ─┐
                   ├──→ 7d451ca5... ──┬──→ 5fps, bars cropped
    /backup/a.mp4 ─┘                  ├──→ 5fps, bars left on
                                      └──→ 3fps, bars cropped

- `files`    have I seen this path, and is it still the same file?
- `content`  what is this video, in itself?
- `sigs`     where is the signature for this content under these settings?

One table cannot hold that shape. Keyed on the content alone it can record only
one name per video, so a second copy loses its path and a report cannot say
which file it means.

Why `files` exists
------------------
It is the reason a run that changes nothing reads nothing. `stat()` costs
microseconds; if the size and mtime match what was recorded, the file at that
path has not been touched and the stored hash still describes it. Without that
step every run reads every byte of the library — measured at 0.82 GB/s locally,
which for a terabyte over SMB is 2.6 hours of finding out that nothing changed.

Identifying by content rather than by path is what makes a rename free. The
earlier scheme named signatures after a hash of the absolute path, so renaming
a file, renaming any folder above it, moving to another disk, or mounting the
same NAS at a different point all threw the whole cache away.

The key is the inputs, the columns are the outputs
--------------------------------------------------
`sigs` is keyed on `(hash, fps, crop_bars, detector)`: four things that change
what comes out of the fingerprinter. `crop_string` is not among them — the same
content, the same detector and the same setting always crop to the same box, so
it is a result, not a condition.

`detector` is in the key to stop a silent fault. A signature taken from a
cropped video is only comparable with one cropped the same way, and
`detect_bars.py` has tunable constants. Retune it without a version and the
cache goes on serving signatures taken from a different picture, with no
symptom — the same class of fault as comparing results from two builds of the
binary, which is what `version.h` exists to prevent.

`ffmpeg` is recorded but deliberately not in the key. MPEG-7 signature is a
standard format and its output should be stable, so re-fingerprinting a whole
library on every ffmpeg upgrade costs more than it protects. Recorded, it can
still answer "which version made this" if a release ever does change the
output, and the invalidation can be done deliberately.

Content-addressed filenames
---------------------------
    7d451ca5bc7e4f1a2b3c4d5e6f7a8b9c_5fps_crop-v1.sig

The name carries the whole key, so the directory describes itself: **losing the
database costs an index, not the signatures**, because the key can be read back
off the filenames. The database is an accelerator, not the only record; what it
holds that the names do not is each signature's crop and its frame count, so a
rebuild has to read every file's header for the count and cannot recover the
crop. There is no rebuild command yet.

Sharing a store between tools takes the same index *and* the same directory:
rows name files relative to the directory, so an index pointed at another one
finds nothing.

Why blake2b rather than XXH3
----------------------------
XXH3 is not in the standard library, and these tools declare no dependencies so
they run under a bare interpreter. Measured, blake2b reaches 0.82 GB/s on a
local SSD while a network share delivers about 0.1 GB/s, so the hash is not
what limits this and a faster one would not show. `digest_size=16` gives 128
bits, the same width as XXH3-128.
"""

import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Read size. Large enough that syscalls do not matter, small enough to stay out
# of the way.
CHUNK = 1 << 22

# Hash width in bytes. 16 = 128 bits = 32 hex characters.
DIGEST_SIZE = 16

# Bump when a column changes meaning or goes away.
#
# 2: files.mtime became files.mtime_ns. Whole seconds let a file rewritten at
#    the same size within the second it was recorded keep its old hash.
#    sigs.frames is now the fine-signature count read out of the file rather
#    than duration x fps rounded, which was off by a frame or two, and
#    sigs.crop_state says what the bar detector concluded, so "no bars" and
#    "could not tell" are no longer one empty string. open_db migrates a
#    schema 1 index in place and keeps every signature.
SCHEMA_VERSION = "2"

# Sizes of the pieces of a binary signature in bits, as ffmpeg's signature
# filter writes them and as the comparison binary reads them: a header up to
# and including the segment count, one coarse signature per segment, a
# compression flag, one fine signature per frame. Every file in the corpus is
# exactly this long.
SIG_HEADER_BITS = 274
SIG_COARSE_BITS = 1344
SIG_FLAG_BITS = 1
SIG_FINE_BITS = 689
SIG_HEADER_BYTES = (SIG_HEADER_BITS + 7) // 8

# What the bar detector concluded about a video, kept beside its signature.
# 'failed' is not among them: a video whose bars could not be looked for is a
# file that failed, not a signature with no bars. 'unknown' is what an index
# from before schema 2 can say about a cropped signature with no crop.
CROP_STATES = ("disabled", "detected", "none", "uncertain", "unknown")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Which content was last seen at this path, and the size and mtime it had.
-- Both unchanged means the stored hash still describes it, so nothing is read.
-- mtime in nanoseconds, as stat reports it: a change within one second at
-- the same size is the case whole seconds could not see.
CREATE TABLE IF NOT EXISTS files (
    path     TEXT PRIMARY KEY,
    hash     TEXT NOT NULL,
    size     INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);

-- Facts about the video itself, independent of where it sits and of any
-- setting used to fingerprint it.
CREATE TABLE IF NOT EXISTS content (
    hash     TEXT PRIMARY KEY,
    size     INTEGER NOT NULL,
    duration REAL NOT NULL
);

-- Content x settings -> signature file. frames is what the file's header
-- says, crop_state is one of CROP_STATES.
CREATE TABLE IF NOT EXISTS sigs (
    hash        TEXT NOT NULL,
    fps         REAL NOT NULL,
    crop_bars   INTEGER NOT NULL,
    detector    TEXT NOT NULL,
    crop_string TEXT NOT NULL,
    crop_state  TEXT NOT NULL,
    frames      INTEGER NOT NULL,
    ffmpeg      TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (hash, fps, crop_bars, detector)
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    """Open the index, creating it and its directory when they are missing.

    A schema 1 index is migrated in place, keeping every signature; see
    migrate_1_to_2. Anything newer than this reader knows is refused.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # The timeout is the whole of the concurrency story: a second process on
    # the same index waits for the lock instead of failing with "database is
    # locked". Two producers on one machine may share an index. Nothing here
    # promises anything across machines or on a network share.
    con = sqlite3.connect(path, timeout=30)
    # WAL so a reader cannot block a writer. Writes here all happen on one
    # thread; reads may come from another process entirely.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    found = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if found is None:
        con.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
        con.commit()
    elif found[0] == "1":
        migrate_1_to_2(con, path)
    elif found[0] != SCHEMA_VERSION:
        raise SystemExit(
            f"{path} is schema {found[0]}, this reads {SCHEMA_VERSION}. The "
            f"columns mean something different, and reading it anyway gives "
            f"answers that look right. Point --db somewhere else, or migrate.")
    return con


def migrate_1_to_2(con: sqlite3.Connection, path: Path) -> None:
    """Bring a schema 1 index up to 2 without losing a signature.

    Path records carried whole-second mtimes. They are scaled to nanoseconds,
    so a file whose mtime is a whole second still hits, and any other file is
    hashed again on its next visit: that reads the file and decodes nothing,
    and it is the only honest thing to do with a record that could not tell
    one second's rewrites apart. Signature rows gain a crop state; a cropped
    signature that recorded no crop is marked unknown rather than none,
    because the old index could not say which it was. Frame counts, which
    were estimates, are corrected from the file's header the first time each
    signature is looked up.
    """
    paths = con.execute("SELECT count(*) FROM files").fetchone()[0]
    sigs = con.execute("SELECT count(*) FROM sigs").fetchone()[0]
    con.executescript("""
        ALTER TABLE files RENAME COLUMN mtime TO mtime_ns;
        UPDATE files SET mtime_ns = mtime_ns * 1000000000;
        ALTER TABLE sigs ADD COLUMN crop_state TEXT NOT NULL DEFAULT 'unknown';
        UPDATE sigs SET crop_state = CASE
            WHEN crop_bars = 0 THEN 'disabled'
            WHEN crop_string != '' THEN 'detected'
            ELSE 'unknown' END;
        UPDATE meta SET value = '2' WHERE key = 'schema';
    """)
    con.commit()
    print(f"{path}: migrated the index from schema 1 to 2. All {sigs} "
          f"signatures are kept. The {paths} path records carried whole-second "
          f"mtimes, so a path whose file has a finer mtime is hashed again on "
          f"its next visit, which reads the file and decodes nothing. Frame "
          f"counts are corrected from each signature's header as it is used.",
          file=sys.stderr)


def hash_file(path: Path) -> str:
    """blake2b-128 of the whole file, as hex."""
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class FileChanged(OSError):
    """The file changed while it was being hashed, so the hash describes no
    version of it that can be recorded."""


def identify(path: Path) -> tuple[str, os.stat_result]:
    """Hash the file, and prove the bytes hashed are the ones the returned
    stat describes: the stat before and after must agree. remember_file takes
    that stat rather than taking a new one, because a new one could describe
    a rewrite that happened after the last byte was read."""
    before = path.stat()
    content_hash = hash_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise FileChanged(f"{path} changed while it was being read")
    return content_hash, before


def _bits(data: bytes, offset: int, count: int) -> int:
    value = 0
    for i in range(offset, offset + count):
        value = (value << 1) | ((data[i >> 3] >> (7 - (i & 7))) & 1)
    return value


def expected_size(frames: int, segments: int) -> int:
    """How many bytes a signature with these counts occupies."""
    bits = (SIG_HEADER_BITS + segments * SIG_COARSE_BITS + SIG_FLAG_BITS
            + frames * SIG_FINE_BITS)
    return (bits + 7) // 8


def read_header(path: Path) -> dict | None:
    """What a signature file says about itself, or None when it cannot be one.

    None covers a missing file, one shorter than a header, zero frames or
    segments, a zero time base, and a file shorter than its own counts need,
    which is what an interrupted ffmpeg leaves behind. It reads one stat and
    35 bytes, never the whole file, so a cache lookup can afford it.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(SIG_HEADER_BYTES)
    except OSError:
        return None
    if len(head) < SIG_HEADER_BYTES:
        return None
    frames = _bits(head, 129, 32)
    timebase_den = _bits(head, 161, 16)
    segments = _bits(head, 242, 32)
    if not frames or not segments or not timebase_den:
        return None
    need = expected_size(frames, segments)
    if size < need:
        return None
    return {"frames": frames, "segments": segments,
            "timebase_den": timebase_den, "size": size, "expected_size": need}


def fps_tag(fps: float) -> str:
    """How an fps is written in a filename.

    Normalised: --fps 5 and --fps 5.0 are the same thing and must not produce
    two names for one signature.
    """
    return f"{fps:g}"


def sig_filename(content_hash: str, fps: float, crop_bars: bool,
                 detector: str) -> str:
    """The filename for one signature, carrying its whole key.

    The detector is left out when nothing is cropped: that signature does not
    depend on it, and naming it there would retire perfectly good files every
    time the detector is retuned.
    """
    crop = f"crop-v{detector}" if crop_bars else "nocrop"
    return f"{content_hash}_{fps_tag(fps)}fps_{crop}.sig"


def known_hash(con: sqlite3.Connection, path: Path,
               stat: os.stat_result | None = None) -> str | None:
    """The content hash for this path, if it is still the file we recorded.

    This is the fast path, and the reason the store is worth having: `stat()`
    takes microseconds, and a matching size and mtime mean the stored hash is
    still right, so not one byte is read. A caller only needs identify() when
    this returns None.

    Size and mtime are a fast check for change, not proof of sameness: a
    rewrite that puts the old size and mtime back on purpose passes it. That
    is the trade the fast path makes, and it is why the mtime is compared to
    the nanosecond rather than the second.
    """
    stat = stat or path.stat()
    row = con.execute("SELECT hash, size, mtime_ns FROM files WHERE path=?",
                      (str(path.resolve()),)).fetchone()
    if row and row[1] == stat.st_size and row[2] == stat.st_mtime_ns:
        return row[0]
    return None


def remember_file(con: sqlite3.Connection, path: Path, content_hash: str,
                  stat: os.stat_result | None = None) -> None:
    """Record which content is at this path, and the stat it had.

    Pass the stat identify() returned: it describes the bytes that were
    hashed. A fresh stat here could describe a rewrite that happened since.
    """
    stat = stat or path.stat()
    con.execute(
        "INSERT INTO files (path, hash, size, mtime_ns) VALUES (?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, "
        "size=excluded.size, mtime_ns=excluded.mtime_ns",
        (str(path.resolve()), content_hash, stat.st_size, stat.st_mtime_ns))


def remember_content(con: sqlite3.Connection, content_hash: str, size: int,
                     duration: float) -> None:
    con.execute(
        "INSERT INTO content (hash, size, duration) VALUES (?,?,?) "
        "ON CONFLICT(hash) DO UPDATE SET size=excluded.size, "
        "duration=excluded.duration",
        (content_hash, size, round(duration, 3)))


def content_facts(con: sqlite3.Connection, content_hash: str) -> dict | None:
    """Size and duration for this content, or None if it was never recorded."""
    row = con.execute("SELECT size, duration FROM content WHERE hash=?",
                      (content_hash,)).fetchone()
    return {"size": row[0], "duration": row[1]} if row else None


def find_signature(con: sqlite3.Connection, sig_dir: Path, content_hash: str,
                   fps: float, crop_bars: bool, detector: str) -> dict | None:
    """The signature for this content under these settings, if it is usable.

    The index saying so is not enough: the file has to be there and has to be
    a whole signature. A sig directory can be cleared, or arrive from another
    machine later than its index, and an interrupted ffmpeg used to leave a
    truncated file that was not empty; none of that shows up in a row. The
    check is the header and a stat, not the whole file.

    The frame count comes from the file, and a row that disagrees is
    corrected: an index from before schema 2 holds duration x fps rounded,
    which the file knows better than.
    """
    key = (content_hash, fps, int(crop_bars), detector if crop_bars else "")
    row = con.execute(
        "SELECT filename, crop_string, crop_state, frames FROM sigs "
        "WHERE hash=? AND fps=? AND crop_bars=? AND detector=?", key).fetchone()
    if not row:
        return None
    path = sig_dir / row[0]
    header = read_header(path)
    if header is None:
        return None
    if header["frames"] != row[3]:
        con.execute(
            "UPDATE sigs SET frames=? WHERE hash=? AND fps=? AND crop_bars=? "
            "AND detector=?", (header["frames"],) + key)
    return {"path": path, "filename": row[0], "crop": row[1],
            "crop_state": row[2], "frames": header["frames"]}


def remember_signature(con: sqlite3.Connection, content_hash: str, fps: float,
                       crop_bars: bool, detector: str, crop_string: str,
                       frames: int, ffmpeg: str, filename: str,
                       crop_state: str) -> None:
    if crop_state not in CROP_STATES:
        raise ValueError(f"crop_state {crop_state!r} is not one of {CROP_STATES}")
    con.execute(
        "INSERT INTO sigs (hash, fps, crop_bars, detector, crop_string, "
        "crop_state, frames, ffmpeg, filename, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(hash, fps, crop_bars, detector) DO UPDATE SET "
        "crop_string=excluded.crop_string, crop_state=excluded.crop_state, "
        "frames=excluded.frames, ffmpeg=excluded.ffmpeg, "
        "filename=excluded.filename, created_at=excluded.created_at",
        (content_hash, fps, int(crop_bars), detector if crop_bars else "",
         crop_string, crop_state, frames, ffmpeg, filename,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))


def note_sig_dir(con: sqlite3.Connection, sig_dir: Path) -> str | None:
    """Record which signature directory this index describes.

    Returns the directory it was recorded with when that is a different
    place, and moves the record on, so a store that was moved whole says so
    once. Sharing an index takes the same directory as well: every row names
    a file relative to it, and an index alone finds nothing.
    """
    current = str(sig_dir.resolve())
    row = con.execute("SELECT value FROM meta WHERE key='sig_dir'").fetchone()
    con.execute(
        "INSERT INTO meta VALUES ('sig_dir', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (current,))
    con.commit()
    if row is None or row[0] == current:
        return None
    return row[0]


def ffmpeg_version(ffmpeg: str) -> str:
    """Which ffmpeg made a signature. Recorded for tracing, not keyed on."""
    try:
        done = subprocess.run([ffmpeg, "-version"], capture_output=True,
                              text=True, timeout=10)
        return done.stdout.strip().splitlines()[0].split(" Copyright")[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""
