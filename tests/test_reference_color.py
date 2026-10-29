"""Known synthetic patches check numeric meaning; not camera or photographic validation."""
import hashlib
import io

from PIL import Image, ImageCms
import pytest

from c1ai.contracts import Refused
from c1ai.reference_color import analyze, image_bytes, load_srgb, validate_anchors


def anchor(kind='neutral'):
    return [{'id': 'surface', 'role': 'same surface', 'kind': kind,
             'reference_box': [0, 0, 1, 1], 'target_box': [0, 0, 1, 1]}]


def encoded(color=(128, 128, 128), *, icc=None, mode='RGB'):
    stream = io.BytesIO()
    Image.new(mode, (32, 32), color).save(stream, format='PNG', **({'icc_profile': icc} if icc else {}))
    return stream.getvalue()


def test_untagged_images_require_explicit_assumption_and_gray_has_no_hue():
    with pytest.raises(Refused, match='Missing ICC'):
        load_srgb(encoded(), False)
    rgb, provenance = load_srgb(encoded(), True)
    report = analyze(rgb, rgb, anchor())
    mapping = report['mappings'][0]
    assert provenance['conversion'] == 'explicit_sRGB_assumption_without_ICC'
    assert mapping['reference']['Lab']['a'] == 0 and mapping['reference']['Lab']['b'] == 0
    assert mapping['reference_minus_target']['hue_degrees'] is None
    assert mapping['reference_minus_target']['preview_patch_deltaE76'] == 0


def test_embedded_icc_is_processed_and_corrupt_profile_is_refused():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    rgb, provenance = load_srgb(encoded(icc=profile), False)
    assert rgb.size == (32, 32) and provenance['conversion'].startswith('embedded_ICC')
    with pytest.raises(Refused, match='Cannot decode'):
        load_srgb(encoded(icc=b'not-an-ICC-profile'), True)


def test_neutral_anchor_preserves_reference_cast_instead_of_forcing_neutral():
    warm, _ = load_srgb(encoded((145, 130, 110)), True)
    neutral, _ = load_srgb(encoded(), True)
    mapping = analyze(warm, neutral, anchor())['mappings'][0]
    assert mapping['reference']['Lab']['b'] > 0
    assert mapping['reference_minus_target']['b'] > 0
    assert 'reference cast' in mapping['neutral_interpretation']


def test_opponent_axis_signs_of_primaries_guard_raw_lab_byte_decoding():
    green = Image.new('RGB', (8, 8), (0, 255, 0))
    blue = Image.new('RGB', (8, 8), (0, 0, 255))
    g = analyze(green, green, anchor('color'))['mappings'][0]['reference']['Lab']
    b = analyze(blue, blue, anchor('color'))['mappings'][0]['reference']['Lab']
    assert g['a'] < -50 and g['b'] > 50
    assert b['a'] > 50 and b['b'] < -50


def test_black_and_white_are_flagged_without_claiming_raw_clipping():
    white, _ = load_srgb(encoded((255, 255, 255)), True)
    report = analyze(white, white, anchor())
    assert 'near_black_or_white_color_unreliable' in report['mappings'][0]['reference']['quality_flags']
    assert report['reference_tones']['bands'][-1]['low_chroma_candidates'] is None


@pytest.mark.parametrize('bounds', [[0, 0, 0, 1], [-.1, 0, .3, .4], [0, 0, True, 1], [0, float('nan'), 1, 1]])
def test_invalid_coordinate_boxes_are_refused(bounds):
    anchors = anchor(); anchors[0]['target_box'] = bounds
    with pytest.raises(Refused):
        validate_anchors(anchors)


def test_transparent_reference_is_not_measured_on_an_invented_background():
    with pytest.raises(Refused, match='Transparency'):
        load_srgb(encoded((120, 120, 120, 0), mode='RGBA'), True)


def test_reference_hash_and_symlink_protection(tmp_path):
    path = tmp_path / 'reference.png';data = encoded();path.write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    assert image_bytes(path, checksum) == data
    path.write_bytes(encoded((200, 200, 200)))
    with pytest.raises(Refused, match='differs'):
        image_bytes(path, checksum)
    link = tmp_path / 'alias.png';link.symlink_to(path)
    with pytest.raises(Refused):
        image_bytes(link, checksum)


def test_conflicting_neutral_brightness_pairs_do_not_become_a_global_curve():
    ref = Image.new('RGB', (32, 32), (80, 80, 80))
    ref.paste((180, 180, 180), (16, 0, 32, 32))
    target = Image.new('RGB', (32, 32), (128, 128, 128))
    anchors = [
        {'id': 'a', 'role': 'gray a', 'kind': 'neutral', 'reference_box': [0, 0, .5, 1], 'target_box': [0, 0, .5, 1]},
        {'id': 'b', 'role': 'gray b', 'kind': 'neutral', 'reference_box': [.5, 0, 1, 1], 'target_box': [.5, 0, 1, 1]},
    ]
    report = analyze(ref, target, anchors)
    assert report['tone_order_conflict'] is True
