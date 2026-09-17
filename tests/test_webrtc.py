from core.webrtc import QUALITY_PRESETS


def test_webrtc_quality_presets_are_ordered():
    widths = [QUALITY_PRESETS[name]['width'] for name in ('low', 'medium', 'high', 'ultra')]
    fps = [QUALITY_PRESETS[name]['fps'] for name in ('low', 'medium', 'high', 'ultra')]
    assert widths == sorted(widths)
    assert fps == sorted(fps)
    assert widths[-1] == 2560
