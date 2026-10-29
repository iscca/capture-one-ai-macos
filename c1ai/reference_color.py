"""Measure explicit rendered-image anchors; never infer RAW coordinates or C1 setters.

ICC conversion uses Pillow/LCMS. Lab is D50, 8-bit encoded by Pillow, so these
measurements are descriptive preview evidence, not camera colorimetry.
"""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path
from statistics import median

from PIL import Image, ImageCms, ImageOps, ImageStat

from .contracts import Refused

MAX_BYTES = 32 * 1024 * 1024
MAX_PIXELS = 40_000_000


def image_bytes(path, expected_hash):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise Refused('invalid_reference_image', 'Use an explicit absolute regular image file of at most 32 MiB')
    data = path.read_bytes()
    if not isinstance(expected_hash, str) or hashlib.sha256(data).hexdigest() != expected_hash:
        raise Refused('image_hash_changed', 'Image differs from the identified reference/checkpoint; do not reuse its measurements')
    return data


def load_srgb(data, assume_srgb):
    """Reject invalid profiles rather than silently comparing different RGB spaces."""
    if type(assume_srgb) is not bool:
        raise Refused('invalid_color_assumption', 'assume_srgb entries must be boolean')
    try:
        with Image.open(io.BytesIO(data)) as original:
            if original.format not in {'JPEG', 'PNG', 'WEBP', 'TIFF'} or getattr(original, 'n_frames', 1) != 1:
                raise Refused('unsupported_reference_image', 'Use a single JPEG/PNG/WebP/TIFF render, not a RAW or animation')
            if original.width * original.height > MAX_PIXELS:
                raise Refused('reference_too_large', 'Use a reference render of at most 40 megapixels')
            original.load()
            profile = original.info.get('icc_profile')
            frame = ImageOps.exif_transpose(original)
            if 'A' in frame.getbands() or 'transparency' in frame.info:
                if frame.convert('RGBA').getchannel('A').getextrema()[0] != 255:
                    raise Refused('transparent_reference', 'Transparency has no defined comparison background')
                frame = frame.convert('RGB')
            if profile:
                frame = ImageCms.profileToProfile(frame, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                    ImageCms.createProfile('sRGB'), outputMode='RGB', renderingIntent=1)
                provenance = 'embedded_ICC_to_sRGB_relative_colorimetric'
            else:
                if not assume_srgb or frame.mode not in {'RGB', 'L'}:
                    raise Refused('color_profile_required', 'Missing ICC: explicitly confirm sRGB for an RGB/gray image, or provide a profiled render')
                frame = frame.convert('RGB')
                provenance = 'explicit_sRGB_assumption_without_ICC'
            return frame, {'conversion': provenance, 'oriented_dimensions': list(frame.size),
                           'working_space': 'sRGB', 'Lab_white_point': 'D50',
                           'intent': 'relative_colorimetric', 'precision': '8-bit image and Lab encoding'}
    except Refused:
        raise
    except Exception as error:
        raise Refused('image_color_decode_failed', 'Cannot decode image/profile: ' + str(error)) from error


def box(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        raise Refused('invalid_anchor_box', 'Use normalized oriented-image bounds [left,top,right,bottom] within 0..1')
    return value


def validate_anchors(anchors):
    if not isinstance(anchors, list) or not 1 <= len(anchors) <= 12:
        raise Refused('invalid_anchors', 'Select one to twelve corresponding detail patches')
    seen = set()
    for anchor in anchors:
        if not isinstance(anchor, dict) or set(anchor) != {'id', 'role', 'kind', 'reference_box', 'target_box'}:
            raise Refused('invalid_anchors', 'Each anchor needs id, role, kind, reference_box, target_box')
        for key in ('id', 'role'):
            if not isinstance(anchor[key], str) or not anchor[key].strip() or len(anchor[key]) > 500:
                raise Refused('invalid_anchors', 'Anchor id and semantic role must be explicit text')
        if anchor['id'] in seen or anchor['kind'] not in {'neutral', 'color'}:
            raise Refused('invalid_anchors', 'Use unique anchor IDs and kind neutral/color')
        seen.add(anchor['id'])
        box(anchor['reference_box']); box(anchor['target_box'])


def lab_image(rgb):
    return ImageCms.profileToProfile(rgb, ImageCms.createProfile('sRGB'), ImageCms.createProfile('LAB'),
                                   outputMode='LAB', renderingIntent=1)


def lab_pixels(rgb):
    values = lab_image(rgb).tobytes()
    # Pillow's raw LAB bytes store signed a/b in two's complement, while
    # getpixel/getdata expose those bands biased by 128. Do not mix encodings.
    return [(l * 100 / 255, a if a < 128 else a - 256, b if b < 128 else b - 256)
            for l, a, b in zip(values[0::3], values[1::3], values[2::3])]


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def lab_summary(pixels):
    if not pixels:
        return None
    columns = list(zip(*pixels))
    l, a, b = [median(column) for column in columns]
    chroma = math.hypot(a, b)
    return {'sample_count': len(pixels), 'L': round(l, 3), 'a': round(a, 3), 'b': round(b, 3),
            'chroma': round(chroma, 3),
            'hue_degrees': round(math.degrees(math.atan2(b, a)) % 360, 3) if chroma >= 5 else None,
            'L_p10': round(percentile(columns[0], .1), 3), 'L_p90': round(percentile(columns[0], .9), 3),
            'a_p10': round(percentile(columns[1], .1), 3), 'a_p90': round(percentile(columns[1], .9), 3),
            'b_p10': round(percentile(columns[2], .1), 3), 'b_p90': round(percentile(columns[2], .9), 3),
            'near_black_white_fraction': round(sum(p[0] < 2 or p[0] > 98 for p in pixels) / len(pixels), 4)}


def tonal_summary(rgb):
    sampled = rgb.copy()
    sampled.thumbnail((384, 384), Image.Resampling.NEAREST)
    pixels = lab_pixels(sampled)
    bands = []
    for low, high in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        values = [p for p in pixels if low <= p[0] and (p[0] < high or high == 100)]
        candidates = [p for p in values if math.hypot(p[1], p[2]) <= 12 and 2 <= p[0] <= 98]
        bands.append({'L_range': [low, high], 'fraction': round(len(values) / len(pixels), 4),
                      'all_content': lab_summary(values), 'low_chroma_candidates': lab_summary(candidates)})
    lights = [p[0] for p in pixels]
    return {'L_percentiles': {str(p): round(percentile(lights, p / 100), 3) for p in (5, 25, 50, 75, 95)},
            'bands': bands, 'sampling': 'nearest subsampling, maximum 384px long edge',
            'interpretation': 'Band colors depend on scene content; low chroma does not identify a physically neutral object or prove white-balance error'}


def patch_stats(rgb, bounds):
    w, h = rgb.size
    pixel_box = [math.floor(bounds[0] * w), math.floor(bounds[1] * h),
                 math.ceil(bounds[2] * w), math.ceil(bounds[3] * h)]
    patch = rgb.crop(tuple(pixel_box))
    if patch.width < 2 or patch.height < 2:
        raise Refused('anchor_too_small', 'Use patches at least 2x2 pixels, not a single color-pick point')
    original_size = list(patch.size)
    patch.thumbnail((128, 128), Image.Resampling.NEAREST)
    summary = lab_summary(lab_pixels(patch))
    flags = []
    if summary['sample_count'] < 25:
        flags.append('few_samples')
    if summary['L_p90'] - summary['L_p10'] > 20:
        flags.append('mixed_lightness_or_texture')
    if max(summary['a_p90'] - summary['a_p10'], summary['b_p90'] - summary['b_p10']) > 10:
        flags.append('mixed_colors')
    if summary['near_black_white_fraction'] > .2:
        flags.append('near_black_or_white_color_unreliable')
    return {'Lab': summary, 'sRGB_median': ImageStat.Stat(patch).median,
            'pixel_box': pixel_box, 'patch_dimensions': original_size, 'quality_flags': flags}


def analyze(reference, target, anchors):
    validate_anchors(anchors)
    mappings, neutral_tones = [], []
    for anchor in anchors:
        ref, cur = patch_stats(reference, anchor['reference_box']), patch_stats(target, anchor['target_box'])
        r, t = ref['Lab'], cur['Lab']
        dl, da, db = (r[k] - t[k] for k in ('L', 'a', 'b'))
        rh, th = r['hue_degrees'], t['hue_degrees']
        mapping = {'id': anchor['id'], 'role': anchor['role'], 'kind': anchor['kind'],
            'reference': ref, 'target': cur,
            'reference_minus_target': {'L': round(dl, 3), 'a': round(da, 3), 'b': round(db, 3),
                'chroma': round(r['chroma'] - t['chroma'], 3),
                'hue_degrees': round((rh - th + 180) % 360 - 180, 3) if rh is not None and th is not None else None,
                'preview_patch_deltaE76': round(math.sqrt(dl * dl + da * da + db * db), 3)},
            'neutral_interpretation': 'Match the reference cast, not zero a/b; neutrality is a client-identified material assumption'
                if anchor['kind'] == 'neutral' else None}
        mappings.append(mapping)
        if anchor['kind'] == 'neutral':
            neutral_tones.append({'id': anchor['id'], 'input_target_L': t['L'], 'output_reference_L': r['L'],
                                  'reference_a': r['a'], 'reference_b': r['b'], 'delta_a': round(da, 3), 'delta_b': round(db, 3)})
    neutral_tones.sort(key=lambda p: p['input_target_L'])
    conflicts = any(b['output_reference_L'] < a['output_reference_L']
                    or (abs(b['input_target_L'] - a['input_target_L']) <= .5
                        and abs(b['output_reference_L'] - a['output_reference_L']) > 1)
                    for a, b in zip(neutral_tones, neutral_tones[1:]))
    return {'reference_tones': tonal_summary(reference), 'target_tones': tonal_summary(target), 'mappings': mappings,
            'neutral_tone_correspondences': neutral_tones, 'tone_order_conflict': conflicts,
            'axis_convention': 'Lab D50: +a red/magenta, -a green, +b yellow, -b blue; deltas are reference minus target',
            'limits': ['Patch medians and DeltaE76 describe rendered-image differences, not similarity percentage or automatic quality approval',
                       'Boxes are oriented-preview coordinates, not Capture One/RAW masks',
                       'No direct conversion of sRGB/Lab deltas into C1 Device RGB, curve points, kelvin or slider values',
                       'Tone pairs are evidence, not a fitted curve; content, light and materials can invalidate correspondences',
                       '8-bit Lab, ICC rendering and JPEG compression limit precision; flags are sampling heuristics, not calibrated acceptance thresholds']}
