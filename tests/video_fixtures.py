"""Small real-FFmpeg fixtures shared by the video integration suites."""
import json
import subprocess


def rotate_90(source, target):
    """Copy the video with a verified 90-degree display matrix, not rotated pixels."""
    help_text = subprocess.run(
        ['ffmpeg', '-hide_banner', '-h', 'full'], check=True,
        capture_output=True, text=True, timeout=30).stdout
    # FFmpeg 6.1 accepts rotate metadata without writing a display matrix.
    # Prefer the input option when available; 5.1 needs the older output tag.
    if any(line.startswith('-display_rotation ') for line in help_text.splitlines()):
        args = ['-display_rotation:v:0', '90', '-i', str(source),
                '-map', '0:v:0', '-c', 'copy']
    else:
        args = ['-i', str(source), '-map', '0:v:0', '-c', 'copy',
                '-metadata:s:v:0', 'rotate=90']
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', *args, str(target)],
                   check=True, capture_output=True, timeout=30)

    # Check the fixture independently of the production geometry probe so a
    # missing rotation is diagnosed here instead of as an autorotation bug.
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream_side_data=rotation', '-of', 'json', str(target)],
        check=True, capture_output=True, text=True, timeout=30)
    streams = json.loads(probe.stdout).get('streams', [])
    rotations = [side.get('rotation') for stream in streams
                 for side in stream.get('side_data_list', [])]
    if 90 not in rotations:
        raise AssertionError(f'Rotated fixture lacks a 90-degree display matrix: {probe.stdout}')
