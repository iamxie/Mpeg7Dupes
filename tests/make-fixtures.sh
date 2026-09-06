#!/usr/bin/env sh
# Regenerates the signature fixtures. Run by hand, never by the test suite.
#
# The fixtures are checked in rather than built during the test because a
# signature depends on how ffmpeg decoded and scaled the video, so generating
# them at test time would make the expected output move with the ffmpeg
# version. Checked in, the test only depends on the comparison code, which is
# what it is there to cover.
#
# Every clip is 30 seconds sampled at 5 fps, so 150 frames and about 14 kB.
# Sources are synthetic, so no video files need to be distributed.
#
#   sh tests/make-fixtures.sh          # needs ffmpeg on PATH

set -eu

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/fixtures"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$out"
cd "$work"

enc="-c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p"

# base    30s of one pattern, the reference every other clip is measured against
# insert  10s of a different pattern, the shared advertisement stand-in
ffmpeg -v error -f lavfi -i "mandelbrot=size=320x180:rate=25" -t 30 $enc base.mp4
ffmpeg -v error -f lavfi -i "testsrc2=size=320x180:rate=25" -t 10 $enc insert.mp4

# scaled     same content at half the width, the everyday re-encode
# excerpt    the middle 12 seconds, an extract of the original
# headinsert the insert, then the excerpt
# tailinsert the excerpt, then the insert
# unrelated  a different pattern entirely, must not match anything
ffmpeg -v error -i base.mp4 -vf scale=160:-2 $enc scaled.mp4
ffmpeg -v error -ss 9 -t 12 -i base.mp4 $enc excerpt.mp4
ffmpeg -v error -i insert.mp4 -i excerpt.mp4 \
    -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0" $enc headinsert.mp4
ffmpeg -v error -i excerpt.mp4 -i insert.mp4 \
    -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0" $enc tailinsert.mp4
ffmpeg -v error -f lavfi -i "life=size=320x180:rate=25:mold=10" -t 30 $enc unrelated.mp4

for name in base scaled excerpt headinsert tailinsert unrelated; do
    ffmpeg -v error -i "$name.mp4" -map 0:v:0 -an \
        -vf "fps=5,signature=filename=$name.bin" -f null -
    cp "$name.bin" "$out/$name.bin"
    printf '%-12s %6s bytes\n' "$name.bin" "$(wc -c < "$name.bin" | tr -d ' ')"
done

echo "Fixtures written to $out"
