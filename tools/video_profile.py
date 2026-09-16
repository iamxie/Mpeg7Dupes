"""Pass 1: cached sampled properties. Pass 2: explain the reported time spans.

These are descriptive SDR measurements, not exposure judgements, calibrated
confidence scores or match filters. The signature producer remains independent.
"""
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import sigstore
from tool_settings import run_tool

VERSION = 'visual-1'
RECIPE = {
    'version': VERSION, 'fps': 2, 'width': 160, 'height': 90,
    'center': [16, 9, 128, 72], 'range': '8-bit full-range SDR luma',
    'low_motion_ydif': 2, 'minimum_low_motion_seconds': 2,
    'dark_yavg': 48, 'dark_yhigh': 80, 'black_pixel_threshold': 32,
    'low_contrast_spread': 24, 'dominant_fraction': .8,
}
RECIPE_ID = hashlib.sha256(json.dumps(RECIPE, sort_keys=True).encode()).hexdigest()
STEP = 1 / RECIPE['fps']
METRICS = {'lavfi.signalstats.YAVG': 'yavg', 'lavfi.signalstats.YLOW': 'ylow',
           'lavfi.signalstats.YHIGH': 'yhigh', 'lavfi.signalstats.YDIF': 'ydif',
           'lavfi.blackframe.pblack': 'dark_pixels_percent'}


class ProfileError(RuntimeError):
    pass


def parse_metadata(text):
    rows = []
    for line in text.splitlines():
        if line.startswith('frame:'):
            found = re.fullmatch(r'frame:(\d+)\s+pts:\S+\s+pts_time:(\S+)\s*', line)
            if not found or int(found[1]) != len(rows):
                raise ProfileError('invalid analysis frame sequence')
            try:
                t = float(found[2])
            except ValueError as exc:
                raise ProfileError('invalid analysis timestamp') from exc
            if not math.isfinite(t) or abs(t - len(rows) * STEP) > .001:
                raise ProfileError('analysis timestamps are not continuous from zero')
            rows.append({'time': t})
        elif '=' in line:
            key, value = line.split('=', 1)
            if key in METRICS:
                try:
                    number = float(value)
                except ValueError as exc:
                    raise ProfileError('invalid analysis metric') from exc
                if (not rows or METRICS[key] in rows[-1] or not math.isfinite(number)
                        or not 0 <= number <= (100 if key.endswith('pblack') else 255)):
                    raise ProfileError('invalid or duplicate analysis metric')
                rows[-1][METRICS[key]] = number
    if not rows or any(set(r) != {'time', *METRICS.values()} for r in rows):
        raise ProfileError('incomplete analysis metadata')
    return rows


def intervals(rows, duration, predicate, minimum=0):
    spans = []
    for i, row in enumerate(rows):
        start, end = row['time'], min(duration, row['time'] + STEP)
        if end <= start or not predicate(row, i):
            continue
        if spans and abs(spans[-1][1] - start) < .001:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return [s for s in spans if s[1] - s[0] >= minimum - 1e-6]


def overlap(spans, start, end):
    return sum(max(0, min(b, end) - max(a, start)) for a, b in spans)


def window(profile, start, end, region='center'):
    """Time-weighted sampled estimates on a half-open interval; no clamping
    a partly unobserved interval into a claim of full measurement."""
    data = profile['regions'][region]
    observed = overlap([[0, profile['duration']]], start, end)
    motion_observed = overlap([[STEP, profile['duration']]], start, end)
    result = {'start': start, 'end': end, 'coverage_fraction': observed / (end - start),
              'observed_seconds': observed, 'motion_observed_seconds': motion_observed}
    for name in ('dark', 'low_motion', 'low_contrast'):
        denom = motion_observed if name == 'low_motion' else observed
        result[name + '_ratio'] = (overlap(data['intervals'][name], start, end) / denom
                                   if denom else None)
    for metric in ('yavg', 'dark_pixels_percent', 'ydif'):
        total, weight = 0., 0.
        for i, row in enumerate(data['samples']):
            if metric == 'ydif' and i == 0:
                continue
            seconds = overlap([[row['time'], min(profile['duration'], row['time'] + STEP)]], start, end)
            total += row[metric] * seconds
            weight += seconds
        result['mean_' + metric] = total / weight if weight else None
    return result


def compile_profile(full, center, duration, color):
    if not full or len(full) != len(center) or not math.isfinite(duration) or duration <= 0:
        raise ProfileError('incomplete analysis regions or duration')
    p = {'status': 'complete', 'duration': duration, 'color': color, 'regions': {}}
    for name, rows in (('full', full), ('center', center)):
        spans = {
            'dark': intervals(rows, duration, lambda r, i: r['yavg'] <= RECIPE['dark_yavg']
                              and r['yhigh'] <= RECIPE['dark_yhigh']),
            'low_motion': intervals(rows, duration, lambda r, i: i > 0 and r['ydif'] <= RECIPE['low_motion_ydif'],
                                    RECIPE['minimum_low_motion_seconds']),
            'low_contrast': intervals(rows, duration, lambda r, i: r['yhigh'] - r['ylow'] <= RECIPE['low_contrast_spread']),
        }
        p['regions'][name] = {'samples': rows, 'intervals': spans}
        p['regions'][name]['summary'] = window(p, 0, duration, name)
    central = p['regions']['center']['summary']
    p['flags'] = {k: central[k + '_ratio'] >= RECIPE['dominant_fraction']
                  if central[k + '_ratio'] is not None else None for k in ('dark', 'low_motion')}
    d, m = p['flags']['dark'], p['flags']['low_motion']
    p['category'] = ('unknown' if d is None or m is None else 'both' if d and m
                     else 'dark' if d else 'low_motion' if m else 'neither')
    p['edge_difference'] = (p['regions']['full']['summary']['dark_ratio'] >= .8 and not d)
    return p


def probe(src, ffprobe):
    done = run_tool([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=pix_fmt,color_range,color_transfer,color_space,duration', '-of', 'json', str(src)],
        capture_output=True, text=True)
    try:
        if done.returncode:
            raise ValueError(done.stderr.strip()[:200])
        info = json.loads(done.stdout)['streams'][0]
        if not info.get('pix_fmt'):
            raise ValueError('pixel format is not recorded')
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProfileError(f'cannot probe analysis colour metadata: {exc}') from exc
    transfer = info.get('color_transfer', 'unknown')
    pixel = info['pix_fmt']
    full = (info['color_range'] == 'pc' if info.get('color_range') in ('pc', 'tv')
            else pixel.startswith(('yuvj', 'rgb', 'bgr', 'gbr', 'gray')))
    info.update(range_assumed=info.get('color_range') not in ('pc', 'tv'),
                transfer_assumed=transfer in ('unknown', 'unspecified'),
                effective_range='pc' if full else 'tv')
    # Non-SDR transfer functions need a separate, validated interpretation.
    info['supported'] = transfer in ('unknown', 'unspecified', 'bt709', 'smpte170m',
                                     'smpte240m', 'bt470m', 'bt470bg', 'iec61966-2-1',
                                     'bt2020-10', 'bt2020-12')
    return info


def build(src, directory, ffmpeg, ffprobe):
    color = probe(src, ffprobe)
    if not color['supported']:
        return {'status': 'unsupported', 'reason': 'HDR or unsupported transfer function: '
                + color.get('color_transfer', 'unknown'), 'color': color}
    with tempfile.TemporaryDirectory(prefix='.visual-', dir=directory) as temp:
        work = Path(temp)
        # Resampling suppresses fine codec noise. Both regions come from the
        # same full-frame decode, before any signature-specific crop.
        stats = 'signalstats,blackframe=amount=0:threshold=32,metadata=mode=print:file='
        graph = (f'[0:v:0]fps=2,scale=160:90:flags=area:in_range={color["effective_range"]}:'
                 'out_range=pc,format=yuv444p,split=2[whole][middle];'
                 f'[whole]{stats}full.txt[f];'
                 f'[middle]crop=128:72:16:9:exact=1,{stats}center.txt[c]')
        done = run_tool([ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-xerror',
                        '-i', str(src.resolve()), '-filter_complex_threads', '1',
                        '-filter_complex', graph, '-map', '[f]', '-map', '[c]', '-an', '-f', 'null', '-'],
                       cwd=work, capture_output=True, text=True)
        if done.returncode:
            raise ProfileError(f'analysis decoder failed ({done.returncode}): {done.stderr.strip()[:300]}')
        try:
            full = parse_metadata((work / 'full.txt').read_text())
            center = parse_metadata((work / 'center.txt').read_text())
        except OSError as exc:
            raise ProfileError('analysis decoder did not produce complete metadata') from exc
    measured = full[-1]['time'] + STEP
    try:
        duration = float(color.get('duration', 'nan'))
    except (ValueError, TypeError):
        duration = math.nan
    known = math.isfinite(duration) and duration > 0
    if known and abs(duration - measured) > STEP + .001:
        raise ProfileError('analysis metadata does not cover the video duration')
    result = compile_profile(full, center, min(duration, measured) if known else measured, color)
    result['duration_estimated'] = not known
    result['filter_graph'] = graph
    return result


def make(src, con, directory, *, content_hash, ffmpeg, ffprobe, ffmpeg_version, overwrite=False):
    """Independent content/recipe cache; signatures are neither changed nor retired."""
    stat = src.stat()
    actual = sigstore.known_hash(con, src, stat)
    if actual is None:
        actual, stat = sigstore.identify(src)
    if actual != content_hash:
        raise ProfileError('video changed between fingerprinting and analysis')
    database = Path(con.execute('PRAGMA database_list').fetchone()[2])
    lock = database.parent / ('.' + database.name + '.locks') / ('visual-' + content_hash + '-' + RECIPE_ID)
    with sigstore.file_lock(lock):
        sigstore.check_unchanged(src, stat)
        row = con.execute('SELECT filename, digest FROM profiles WHERE hash=? AND recipe=?',
                          (content_hash, RECIPE_ID)).fetchone()
        if row and not overwrite:
            try:
                raw = (directory / row[0]).read_bytes()
                if hashlib.sha256(raw).hexdigest() == row[1]:
                    p = json.loads(raw)
                    if p['content_hash'] == content_hash and p['recipe_id'] == RECIPE_ID:
                        return dict(p, cache={'filename': row[0], 'sha256': row[1]})
            except (OSError, ValueError, KeyError):
                pass
        p = build(src, directory, ffmpeg, ffprobe)
        p.update(version=VERSION, recipe=RECIPE, recipe_id=RECIPE_ID, content_hash=content_hash,
                 ffmpeg=ffmpeg_version, generated_at=datetime.now(timezone.utc).isoformat())
        raw = (json.dumps(p, ensure_ascii=False, allow_nan=False) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        name = f'{content_hash}.{VERSION}.gen-{secrets.token_hex(16)}.json'
        path = directory / name
        try:
            with path.open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            sigstore.check_unchanged(src, stat)
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO profiles VALUES (?,?,?,?) ON CONFLICT(hash,recipe) '
                        'DO UPDATE SET filename=excluded.filename,digest=excluded.digest',
                        (content_hash, RECIPE_ID, name, digest))
            con.commit()
        except BaseException:
            con.rollback()
            path.unlink(missing_ok=True)
            raise
        return dict(p, cache={'filename': name, 'sha256': digest})


def public(profile):
    """JSON retains intervals/summaries; detailed samples live in the hashed cache artifact."""
    return {**profile, **({'regions': {name: {k: v for k, v in region.items() if k != 'samples'}
                                     for name, region in profile['regions'].items()}}
                         if 'regions' in profile else {})}


def describe(profile):
    if profile.get('status') != 'complete':
        return 'Visual analysis ' + profile.get('status', 'unavailable') + ': ' + profile.get('reason', 'not recorded')
    s = profile['regions']['center']['summary']
    motion = 'unknown' if s['low_motion_ratio'] is None else f"{s['low_motion_ratio']:.0%}"
    return (f"Sampled central picture: low change {motion}, dark {s['dark_ratio']:.0%}, "
            f"low contrast {s['low_contrast_ratio']:.0%} of observed time; "
            'descriptive properties, not an exposure or match-quality verdict.')


def assess(hit, source, candidate):
    result = {'status': 'available', 'scope': 'central 80% of original frames at reported match times',
              'content_notes': [], 'position_notes': [], 'review_recommended': False}
    if source.get('status') == candidate.get('status') == 'disabled':
        return dict(result, status='disabled')
    if hit.get('overrun'):
        result.update(status='unavailable', review_recommended=True)
        result['position_notes'].append('Reported bounds overrun the video; matched-span properties were not evaluated.')
        return result
    for side, p, start, end in (
        ('source', source, hit['source_begin_seconds'], hit['source_end_seconds']),
        ('candidate', candidate, hit['start_seconds'], hit['end_seconds'])):
        if p.get('status') != 'complete':
            result['status'] = 'unavailable'
            result['content_notes'].append(f'{side} analysis unavailable: ' + p.get('reason', p.get('status', 'not recorded')))
            continue
        s = window(p, start, end + 1 / hit['fps'])
        result[side] = s
        if s['coverage_fraction'] < .9:
            if result['status'] == 'available':
                result['status'] = 'partial'
            result['content_notes'].append(f'{side}: reported span extends beyond measured analysis; no dominant-property judgement.')
            continue
        if s['low_motion_ratio'] is None:
            if result['status'] == 'available':
                result['status'] = 'partial'
            result['position_notes'].append(f'{side}: insufficient temporal observations to assess visual change.')
        dark = s['dark_ratio'] is not None and s['dark_ratio'] >= RECIPE['dominant_fraction']
        still = s['low_motion_ratio'] is not None and s['low_motion_ratio'] >= RECIPE['dominant_fraction']
        if dark:
            result['content_notes'].append(f'{side} matched span is predominantly dark; compare visible details, not only light/dark layout.')
        if still:
            result['position_notes'].append(f'{side} matched span has predominantly low visual change; verify the reported start/end positions.')
        if dark and still:
            result['content_notes'].append(f'{side} matched span is both dark and low-change; prioritise visual review.')
        if s['low_contrast_ratio'] is not None and s['low_contrast_ratio'] >= RECIPE['dominant_fraction']:
            result['content_notes'].append(f'{side} matched span has low luminance contrast; this does not measure semantic detail.')
        result['review_recommended'] |= dark or still
    if result['status'] != 'available':
        result['review_recommended'] = True
    return result
