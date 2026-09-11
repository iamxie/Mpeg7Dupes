"""Unit tests for the signature store.

    sh tests/tools.sh          # or: uv run tests/unit/test_sigstore.py

Covers when a signature may be reused and when it must not be. Nothing here
runs ffmpeg; real SQLite files are created under a temporary directory.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sigstore  # noqa: E402
from fakesig import fake_signature  # noqa: E402


# The schema this store wrote before version 2, so a migration can be tested
# against a real one rather than against a guess.
SCHEMA_1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files (path TEXT PRIMARY KEY, hash TEXT NOT NULL,
    size INTEGER NOT NULL, mtime INTEGER NOT NULL);
CREATE INDEX files_hash ON files(hash);
CREATE TABLE content (hash TEXT PRIMARY KEY, size INTEGER NOT NULL,
    duration REAL NOT NULL);
CREATE TABLE sigs (hash TEXT NOT NULL, fps REAL NOT NULL,
    crop_bars INTEGER NOT NULL, detector TEXT NOT NULL,
    crop_string TEXT NOT NULL, frames INTEGER NOT NULL, ffmpeg TEXT NOT NULL,
    filename TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (hash, fps, crop_bars, detector));
INSERT INTO meta VALUES ('schema', '1');
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.con = sigstore.open_db(self.dir / "sig.sqlite")
        self.sig_dir = self.dir / "sig"
        self.sig_dir.mkdir()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def a_video(self, name="a.mp4", data=b"video bytes"):
        path = self.dir / name
        path.write_bytes(data)
        return path


class FpsTag(Base):
    def test_integer_fps_has_no_decimal_point(self):
        """--fps 5 and --fps 5.0 are one thing and must not name two files."""
        self.assertEqual(sigstore.fps_tag(5.0), "5")
        self.assertEqual(sigstore.fps_tag(5), "5")

    def test_fractional_fps_survives(self):
        self.assertEqual(sigstore.fps_tag(2.5), "2.5")


class SigFilename(Base):
    def test_carries_the_whole_key(self):
        """The directory has to describe itself, so the DB can be rebuilt."""
        self.assertEqual(sigstore.sig_filename("abc123", 5.0, True, "1"),
                         "abc123_5fps_crop-v1.sig")

    def test_not_cropping_leaves_the_detector_out(self):
        """That signature does not depend on the detector, so a retune must
        not retire it."""
        self.assertEqual(sigstore.sig_filename("abc123", 5.0, False, "1"),
                         "abc123_5fps_nocrop.sig")

    def test_fps_is_normalised_here_too(self):
        self.assertEqual(sigstore.sig_filename("abc", 5, True, "1"),
                         sigstore.sig_filename("abc", 5.0, True, "1"))


class KnownHash(Base):
    def test_unseen_path_is_not_known(self):
        self.assertIsNone(sigstore.known_hash(self.con, self.a_video()))

    def test_unchanged_file_is_known_without_reading_it(self):
        video = self.a_video()
        sigstore.remember_file(self.con, video, "deadbeef")
        self.assertEqual(sigstore.known_hash(self.con, video), "deadbeef")

    def test_changed_size_is_not_known(self):
        """Different content at a known path must not inherit its hash."""
        video = self.a_video()
        sigstore.remember_file(self.con, video, "deadbeef")
        video.write_bytes(b"different content entirely")
        self.assertIsNone(sigstore.known_hash(self.con, video))

    def test_changed_mtime_is_not_known(self):
        """Content swapped for something of exactly the same length."""
        video = self.a_video()
        sigstore.remember_file(self.con, video, "deadbeef")
        stat = video.stat()
        os.utime(video, (stat.st_atime, stat.st_mtime + 120))
        self.assertIsNone(sigstore.known_hash(self.con, video))

    def test_same_size_rewrite_within_the_second_is_not_known(self):
        """The case whole seconds could not see: same length, same second,
        different bytes. The mtime is compared to the nanosecond."""
        video = self.a_video(data=b"version one")
        sigstore.remember_file(self.con, video, "deadbeef")
        stat = video.stat()
        video.write_bytes(b"version two")
        os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))
        self.assertIsNone(sigstore.known_hash(self.con, video))

    def test_identify_returns_the_stat_of_what_was_hashed(self):
        video = self.a_video(data=b"x" * 100)
        digest, stat = sigstore.identify(video)
        self.assertEqual(digest, sigstore.hash_file(video))
        self.assertEqual((stat.st_size, stat.st_mtime_ns),
                         (video.stat().st_size, video.stat().st_mtime_ns))

    def test_remember_file_records_the_stat_it_is_given(self):
        """The stat that describes the hashed bytes, not a fresh one that
        could describe a rewrite since."""
        video = self.a_video(data=b"x" * 100)
        digest, stat = sigstore.identify(video)
        video.write_bytes(b"y" * 100)
        os.utime(video, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000))
        sigstore.remember_file(self.con, video, digest, stat)
        self.assertIsNone(sigstore.known_hash(self.con, video),
                          "the row must describe the old bytes, which are gone")

    def test_remembering_twice_updates_rather_than_duplicates(self):
        video = self.a_video()
        sigstore.remember_file(self.con, video, "aaa")
        sigstore.remember_file(self.con, video, "bbb")
        self.assertEqual(self.con.execute("SELECT hash FROM files").fetchall(),
                         [("bbb",)])

    def test_two_paths_can_share_one_content(self):
        """A copy of a video needs a second path, not a second signature."""
        one, two = self.a_video("one.mp4"), self.a_video("two.mp4")
        sigstore.remember_file(self.con, one, "same")
        sigstore.remember_file(self.con, two, "same")
        self.assertEqual(
            self.con.execute("SELECT DISTINCT hash FROM files").fetchall(),
            [("same",)])


class HashFile(Base):
    def test_same_bytes_same_hash(self):
        a = self.a_video("a.mp4", b"x" * 5000)
        b = self.a_video("b.mp4", b"x" * 5000)
        self.assertEqual(sigstore.hash_file(a), sigstore.hash_file(b))

    def test_different_bytes_different_hash(self):
        a = self.a_video("a.mp4", b"x" * 5000)
        b = self.a_video("b.mp4", b"y" * 5000)
        self.assertNotEqual(sigstore.hash_file(a), sigstore.hash_file(b))

    def test_is_128_bits(self):
        """Same width as the XXH3-128 this stands in for."""
        self.assertEqual(len(sigstore.hash_file(self.a_video())), 32)

    def test_reads_past_one_chunk(self):
        big = self.a_video("big.mp4", b"ab" * sigstore.CHUNK)
        small = self.a_video("small.mp4", b"ab")
        self.assertNotEqual(sigstore.hash_file(big), sigstore.hash_file(small))


class FindSignature(Base):
    def remember(self, **over):
        args = dict(content_hash="abc", fps=5.0, crop_bars=True, detector="1",
                    crop_string="", frames=500, ffmpeg="ffmpeg 8", filename="",
                    crop_state="none")
        args.update(over)
        args["filename"] = args["filename"] or sigstore.sig_filename(
            args["content_hash"], args["fps"], args["crop_bars"],
            args["detector"])
        sigstore.remember_signature(self.con, **args)
        return args["filename"]

    def on_disk(self, name, data=None, frames=500):
        """A signature-shaped file, unless data says otherwise."""
        (self.sig_dir / name).write_bytes(
            fake_signature(frames=frames) if data is None else data)

    def find(self, **over):
        args = dict(content_hash="abc", fps=5.0, crop_bars=True, detector="1")
        args.update(over)
        return sigstore.find_signature(self.con, self.sig_dir, **args)

    def test_found_when_row_and_file_both_exist(self):
        self.on_disk(self.remember())
        self.assertIsNotNone(self.find())

    def test_row_without_the_file_is_not_found(self):
        """A sig directory can be cleared, or arrive after its index."""
        self.remember()
        self.assertIsNone(self.find())

    def test_empty_file_is_not_found(self):
        """An interrupted ffmpeg leaves a zero byte file behind."""
        self.on_disk(self.remember(), data=b"")
        self.assertIsNone(self.find())

    def test_truncated_file_is_not_found(self):
        """Not empty, but shorter than its own header says. This is what an
        interrupted ffmpeg leaves when it got past the header, and it used to
        count as a cache hit."""
        whole = fake_signature(frames=500)
        self.on_disk(self.remember(), data=whole[:len(whole) // 2])
        self.assertIsNone(self.find())

    def test_a_file_that_is_not_a_signature_is_not_found(self):
        self.on_disk(self.remember(), data=b"x" * 4000)
        self.assertIsNone(self.find())

    def test_frames_come_from_the_file_and_correct_the_row(self):
        """A row from before schema 2 holds duration x fps rounded; the file
        knows better, and the row is fixed on the way past."""
        self.on_disk(self.remember(frames=503), frames=500)
        self.assertEqual(self.find()["frames"], 500)
        self.assertEqual(self.con.execute("SELECT frames FROM sigs").fetchone(),
                         (500,))

    def test_carries_the_crop_state_back(self):
        self.on_disk(self.remember(crop_state="uncertain"))
        self.assertEqual(self.find()["crop_state"], "uncertain")

    def test_an_unknown_crop_state_is_refused(self):
        with self.assertRaises(ValueError):
            self.remember(crop_state="failed")

    def test_a_different_fps_is_a_different_signature(self):
        self.on_disk(self.remember())
        self.assertIsNone(self.find(fps=3.0))

    def test_a_different_crop_setting_is_a_different_signature(self):
        self.on_disk(self.remember())
        self.assertIsNone(self.find(crop_bars=False))

    def test_a_bumped_detector_invalidates_a_cropped_signature(self):
        """It was taken from a different picture, and nothing else says so."""
        self.on_disk(self.remember())
        self.assertIsNone(self.find(detector="2"))

    def test_a_bumped_detector_leaves_uncropped_signatures_alone(self):
        self.on_disk(self.remember(crop_bars=False, detector="1"))
        self.assertIsNotNone(self.find(crop_bars=False, detector="2"))

    def test_carries_the_crop_and_the_frame_count_back(self):
        self.on_disk(self.remember(crop_string="crop=iw:180:0:40", frames=390),
                     frames=390)
        found = self.find()
        self.assertEqual(found["crop"], "crop=iw:180:0:40")
        self.assertEqual(found["frames"], 390)

    def test_remembering_twice_updates_rather_than_duplicates(self):
        self.remember()
        self.remember(frames=999)
        self.assertEqual(self.con.execute("SELECT frames FROM sigs").fetchall(),
                         [(999,)])


class Content(Base):
    def test_stored_and_updated(self):
        sigstore.remember_content(self.con, "abc", 1000, 12.3456)
        sigstore.remember_content(self.con, "abc", 1000, 99.0)
        self.assertEqual(
            self.con.execute("SELECT size, duration FROM content").fetchall(),
            [(1000, 99.0)])

    def test_duration_is_rounded(self):
        sigstore.remember_content(self.con, "abc", 1, 12.3456789)
        self.assertEqual(sigstore.content_facts(self.con, "abc")["duration"],
                         12.346)

    def test_unknown_content_has_no_facts(self):
        self.assertIsNone(sigstore.content_facts(self.con, "never seen"))


class Header(Base):
    def test_reads_the_counts_back(self):
        path = self.sig_dir / "x.sig"
        path.write_bytes(fake_signature(frames=390, segments=9, timebase_den=5))
        header = sigstore.read_header(path)
        self.assertEqual((header["frames"], header["segments"],
                          header["timebase_den"]), (390, 9, 5))
        self.assertEqual(header["size"], header["expected_size"])

    def test_the_size_rule_matches_the_comparison_binary(self):
        """274 + 1344 per segment + 1 + 689 per frame bits, rounded up. Every
        signature in the benchmark corpus is exactly this long."""
        self.assertEqual(sigstore.expected_size(3000, 67), 269666)
        self.assertEqual(sigstore.expected_size(150, 4), 13626)

    def test_missing_short_and_empty_are_none(self):
        path = self.sig_dir / "x.sig"
        self.assertIsNone(sigstore.read_header(path))
        path.write_bytes(b"")
        self.assertIsNone(sigstore.read_header(path))
        path.write_bytes(b"\x00" * 20)
        self.assertIsNone(sigstore.read_header(path))

    def test_zero_counts_are_none(self):
        path = self.sig_dir / "x.sig"
        path.write_bytes(fake_signature(frames=0, segments=1))
        self.assertIsNone(sigstore.read_header(path))
        path.write_bytes(fake_signature(frames=10, segments=0))
        self.assertIsNone(sigstore.read_header(path))


class SigDir(Base):
    def test_first_note_records_and_says_nothing(self):
        self.assertIsNone(sigstore.note_sig_dir(self.con, self.sig_dir))

    def test_the_same_directory_again_says_nothing(self):
        sigstore.note_sig_dir(self.con, self.sig_dir)
        self.assertIsNone(sigstore.note_sig_dir(self.con, self.sig_dir))

    def test_another_directory_names_the_previous_one_once(self):
        """The index names files relative to its directory, so an index used
        with another directory finds nothing, and should say so."""
        other = self.dir / "elsewhere"
        other.mkdir()
        sigstore.note_sig_dir(self.con, self.sig_dir)
        self.assertEqual(sigstore.note_sig_dir(self.con, other),
                         str(self.sig_dir.resolve()))
        self.assertIsNone(sigstore.note_sig_dir(self.con, other))


class Schema(Base):
    def test_reopening_is_fine(self):
        path = self.dir / "again.sqlite"
        sigstore.open_db(path).close()
        sigstore.open_db(path).close()

    def schema_1_index(self, video_mtime_s):
        path = self.dir / "old.sqlite"
        con = sqlite3.connect(path)
        con.executescript(SCHEMA_1)
        con.execute("INSERT INTO files VALUES (?,?,?,?)",
                    (str((self.dir / "a.mp4").resolve()), "abc", 11, video_mtime_s))
        con.execute("INSERT INTO content VALUES ('abc', 11, 12.5)")
        con.execute("INSERT INTO sigs VALUES ('abc', 5.0, 1, '1', "
                    "'crop=iw:180:0:40', 63, 'ffmpeg 5', 'abc_5fps_crop-v1.sig', 't')")
        con.execute("INSERT INTO sigs VALUES ('abc', 5.0, 0, '', '', 63, "
                    "'ffmpeg 5', 'abc_5fps_nocrop.sig', 't')")
        con.execute("INSERT INTO sigs VALUES ('def', 5.0, 1, '1', '', 63, "
                    "'ffmpeg 5', 'def_5fps_crop-v1.sig', 't')")
        con.commit()
        con.close()
        return path

    def test_schema_1_is_migrated_and_keeps_every_signature(self):
        path = self.schema_1_index(1_700_000_000)
        con = sigstore.open_db(path)
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='schema'")
                         .fetchone(), ("2",))
        self.assertEqual(con.execute("SELECT count(*) FROM sigs").fetchone(), (3,))
        self.assertEqual(con.execute("SELECT mtime_ns FROM files").fetchone(),
                         (1_700_000_000 * 1_000_000_000,))
        states = dict(con.execute(
            "SELECT filename, crop_state FROM sigs").fetchall())
        self.assertEqual(states, {
            "abc_5fps_crop-v1.sig": "detected",   # it recorded a crop
            "abc_5fps_nocrop.sig": "disabled",    # cropping was off
            "def_5fps_crop-v1.sig": "unknown",    # cropped, no crop: none or uncertain, it could not say
        })
        con.close()

    def test_migrated_path_hits_only_when_the_mtime_is_a_whole_second(self):
        """Scaled seconds are compared to the nanosecond like any other row:
        a file with a finer mtime is hashed again, one without still hits."""
        video = self.a_video()
        os.utime(video, ns=(1_700_000_000 * 10**9, 1_700_000_000 * 10**9))
        con = sigstore.open_db(self.schema_1_index(1_700_000_000))
        self.assertEqual(sigstore.known_hash(con, video), "abc")
        os.utime(video, ns=(1_700_000_000 * 10**9, 1_700_000_000 * 10**9 + 250))
        self.assertIsNone(sigstore.known_hash(con, video))
        con.close()

    def test_migration_happens_once(self):
        path = self.schema_1_index(1)
        sigstore.open_db(path).close()
        con = sigstore.open_db(path)
        self.assertEqual(con.execute("SELECT count(*) FROM files").fetchone(), (1,))
        con.close()

    def test_a_schema_from_the_future_is_refused(self):
        """Reading it anyway gives answers that look right and are not."""
        path = self.dir / "future.sqlite"
        sigstore.open_db(path).close()
        con = sqlite3.connect(path)
        con.execute("UPDATE meta SET value='99' WHERE key='schema'")
        con.commit()
        con.close()
        with self.assertRaises(SystemExit):
            sigstore.open_db(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
