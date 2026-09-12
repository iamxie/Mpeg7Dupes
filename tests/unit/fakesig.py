"""Test helpers: a signature-shaped file, and stand-ins for ffmpeg and ffprobe.

    from fakesig import fake_signature, write_fake_tools

fake_signature() carries the three header fields the tools read, at the bit
offsets ffmpeg's signature filter puts them, padded to the size those counts
require. Nothing else in it means anything; the comparison binary is tested on
real fixtures, not on these.

write_fake_tools() writes two shell scripts that behave like ffmpeg and ffprobe
as far as sigmake and detect_bars call them, driven by environment variables:

    FAKE_FFMPEG    ok | fail | truncate | empty     what the ffmpeg stand-in does
    FAKE_SIG       path of the signature it copies into place when ok
    FAKE_LOG       every invocation of either tool is appended here, one line
    FAKE_DURATION  what ffprobe reports (default 12.5)
    FAKE_PROBE     ok | fail
    FAKE_BAD       a substring; a path containing it makes ffprobe fail

write_fake_mpeg7dupes() writes a stand-in for the comparison binary, for
find_reuse.py's -l candidates.txt -n source.txt call: one CSV row per
candidate. The first candidate in the list gets FAKE_M7D_FIRST frames
(default 60), the rest FAKE_M7D_REST (default 10), at speed ratio
FAKE_M7D_RATIO (default 1.000000); FAKE_M7D_FAIL=1 makes it exit 1.
"""

import os
import stat
from pathlib import Path

SIG_HEADER_BITS = 274
SIG_COARSE_BITS = 1344
SIG_FLAG_BITS = 1
SIG_FINE_BITS = 689


def _set_bits(buf: bytearray, offset: int, count: int, value: int) -> None:
    for i in range(count):
        bit = (value >> (count - 1 - i)) & 1
        pos = offset + i
        if bit:
            buf[pos >> 3] |= 0x80 >> (pos & 7)


def fake_signature(frames: int = 500, segments: int = 12,
                   timebase_den: int = 5) -> bytes:
    bits = (SIG_HEADER_BITS + segments * SIG_COARSE_BITS + SIG_FLAG_BITS
            + frames * SIG_FINE_BITS)
    buf = bytearray((bits + 7) // 8)
    _set_bits(buf, 0, 32, 1)
    _set_bits(buf, 32, 1, 1)
    _set_bits(buf, 177, 1, 1)
    _set_bits(buf, 210, 32, max(0, frames - 1))
    _set_bits(buf, 129, 32, frames)
    _set_bits(buf, 161, 16, timebase_den)
    _set_bits(buf, 242, 32, segments)
    return bytes(buf)


FFMPEG = r'''#!/bin/sh
# ffmpeg stand-in for tests; see fakesig.py.
printf 'ffmpeg %s\n' "$*" >> "${FAKE_LOG:-/dev/null}"
name=""
prev=""
for arg in "$@"; do
    # Only a fingerprinting call names an output; the detector's -vf does not,
    # and treating it as one wrote a file named after the filter into cwd.
    if [ "$prev" = "-vf" ]; then
        case "$arg" in *signature=filename=*) name="${arg##*signature=filename=}" ;; esac
    fi
    prev="$arg"
done
case "${FAKE_FFMPEG:-ok}" in
    ok)       [ -n "$name" ] && cp "$FAKE_SIG" "$name" ;;
    truncate) [ -n "$name" ] && head -c 200 "$FAKE_SIG" > "$name" ;;
    empty)    [ -n "$name" ] && : > "$name" ;;
    fail)     echo "fake ffmpeg: failing on request" >&2; exit 1 ;;
esac
exit 0
'''

FFPROBE = r'''#!/bin/sh
# ffprobe stand-in for tests; see fakesig.py.
printf 'ffprobe %s\n' "$*" >> "${FAKE_LOG:-/dev/null}"
if [ "${FAKE_PROBE:-ok}" = fail ]; then echo "fake ffprobe: failing on request" >&2; exit 1; fi
if [ -n "${FAKE_BAD:-}" ]; then case "$*" in *"$FAKE_BAD"*) echo "fake ffprobe: cannot read $FAKE_BAD" >&2; exit 1;; esac; fi
case "$*" in
    *"-of json"*) printf '{"streams":[{"height":180}],"format":{"duration":"%s"}}\n' "${FAKE_DURATION:-12.5}" ;;
    *) printf '%s\n' "${FAKE_DURATION:-12.5}" ;;
esac
'''


def write_fake_tools(directory: Path) -> tuple[str, str]:
    """Write the two stand-ins into directory and return their paths."""
    paths = []
    for name, text in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        path = directory / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        paths.append(str(path))
    return paths[0], paths[1]


def signature_env(sig_template: Path, log: Path, **over) -> dict:
    """An environment for the stand-ins: ok ffmpeg copying sig_template."""
    env = dict(os.environ)
    env.update({"FAKE_FFMPEG": "ok", "FAKE_SIG": str(sig_template),
                "FAKE_LOG": str(log), "FAKE_PROBE": "ok",
                "FAKE_DURATION": "12.5"})
    env.update(over)
    return env


MPEG7DUPES = r'''#!/bin/sh
# mpeg7dupes stand-in for tests; see fakesig.py. Run from the signature
# directory with -l candidates.txt -n source.txt, as find_reuse.py does.
printf 'mpeg7dupes %s\n' "$*" >> "${FAKE_LOG:-/dev/null}"
case "$*" in *--version*) echo "mpeg7dupes v0.1 b4 (fake)"; exit 0;; esac
if [ "${FAKE_M7D_FAIL:-0}" = 1 ]; then echo "fake mpeg7dupes: failing on request" >&2; exit 1; fi
prev=""
for arg in "$@"; do
    case "$prev" in -l) candidates="$arg" ;; -n) sources="$arg" ;; esac
    prev="$arg"
done
src=$(cat "$sources")
echo "First signature,Second signature,score,matchframes,goodframes,totalframes,offset,framerateratio,meandist,time 1 [s],time 2 [s],begin 1 [s],end 1 [s],begin 2 [s],end 2 [s],whole"
n=0
while read -r cand; do
    n=$((n + 1))
    if [ $n -eq 1 ]; then f=${FAKE_M7D_FIRST:-60}; else f=${FAKE_M7D_REST:-10}; fi
    end=$(( f / 5 ))
    echo "$src,$cand,100,$f,$f,$f,-3,${FAKE_M7D_RATIO:-1.000000},5.00,1.00,7.00,0.00,$end.00,6.00,$(( end + 6 )).00,1"
done < "$candidates"
'''


def write_fake_mpeg7dupes(directory: Path) -> str:
    """Write the comparison stand-in into directory and return its path."""
    path = directory / "mpeg7dupes"
    path.write_text(MPEG7DUPES)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)
