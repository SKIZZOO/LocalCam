from core.nvr import quality_rtsp_url


def test_live_quality_never_replaces_explicit_channel_feed():
    main = "rtsp://192.168.0.45:554/live/ch00_0"
    sub = "rtsp://192.168.0.45:554/live/ch00_1"

    for quality in ("low", "medium", "high", "ultra"):
        assert quality_rtsp_url(main, quality) == main
        assert quality_rtsp_url(sub, quality) == sub


def test_live_quality_never_replaces_v380_stream_selection():
    main = "rtsp://camera:554/user=admin&password=x&channel=1&stream=0.sdp"
    sub = "rtsp://camera:554/user=admin&password=x&channel=1&stream=1.sdp"

    for quality in ("low", "medium", "high", "ultra"):
        assert quality_rtsp_url(main, quality) == main
        assert quality_rtsp_url(sub, quality) == sub
