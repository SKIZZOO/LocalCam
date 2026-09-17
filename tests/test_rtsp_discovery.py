from core.rtsp import CHANNEL_RTSP_PATHS, _channel_candidates


def test_all_iCSee_channel_variants_are_discovered():
    assert CHANNEL_RTSP_PATHS == (
        '/live/ch00_0',
        '/live/ch00_1',
        '/live/ch01_0',
        '/live/ch01_1',
    )


def test_channel_candidates_include_second_camera_feed():
    base = 'rtsp://192.168.0.50:554'
    candidates = _channel_candidates(base)
    assert base + '/live/ch01_0' in candidates
    assert base + '/live/ch01_1' in candidates
