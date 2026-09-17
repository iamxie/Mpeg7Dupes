"""Shared input validation, applied after TOML and command-line overrides."""
import math


def validate(settings):
    if settings.get('crop_mode', 'motion') not in ('motion', 'black'):
        raise ValueError('crop_mode must be motion or black')
    for key in ('fps', 'min_coverage', 'min_source_coverage'):
        if key not in settings:
            continue
        value = settings[key]
        if key != 'fps' and value is None:
            continue  # The inactive coverage option after precedence resolution.
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
        if key == 'fps' and value <= 0:
            raise ValueError('fps must be greater than zero')
        if key != 'fps' and not 0 <= value <= 100:
            raise ValueError(f'{key} must be between 0 and 100')
    for key in ('jobs', 'thxh', 'limit'):
        if key in settings and (type(settings[key]) is not int or
                                not 0 <= settings[key] <= 2147483647):
            raise ValueError(f'{key} must be an integer between 0 and 2147483647')
    for key in ('crop_bars', 'crops', 'coarse_filter', 'overwrite', 'crop_fallback', 'analyze'):
        if key in settings and type(settings[key]) is not bool:
            raise ValueError(f'{key} must be true or false')
    if settings.get('crop_fallback') and settings.get('crop_bars', True):
        raise ValueError('crop_fallback requires --no-crop-bars (crop_bars = false) for a full-frame baseline')
    for key in ('ffmpeg', 'ffprobe', 'mpeg7dupes', 'hwaccel'):
        if key in settings and (not isinstance(settings[key], str) or
                                (key != 'hwaccel' and not settings[key].strip())):
            raise ValueError(f'{key} must be a nonempty program name or path')
    extensions = settings.get('extensions')
    if not isinstance(extensions, list) or not extensions or any(
            not isinstance(e, str) or not e.lstrip('.') or
            any(c in e for c in '/\\\r\n') for e in extensions):
        raise ValueError('extensions must be a nonempty list of file extensions')


class ToolError(RuntimeError):
    """The program could not be started, rather than rejecting one video."""


def run_tool(command, **kwargs):
    import subprocess
    try:
        return subprocess.run(command, **kwargs)
    except OSError as exc:
        raise ToolError(f'cannot start {command[0]}: {exc}') from exc
