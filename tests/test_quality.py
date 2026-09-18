from core.nvr import quality_rtsp_url


def test_xmeye_preserves_explicit_feed_for_high_quality():
    sub = "rtsp://192.168.0.45:554/live/ch01_1"
    assert quality_rtsp_url(sub, "medium") == sub
    assert quality_rtsp_url(sub, "high") == sub
    assert quality_rtsp_url(sub, "ultra") == sub

    main = "rtsp://192.168.0.45:554/live/ch00_0"
    assert quality_rtsp_url(main, "low") == "rtsp://192.168.0.45:554/live/ch00_1"
    assert quality_rtsp_url(main, "high") == main
    assert quality_rtsp_url(main, "ultra") == main


def test_v380_stream_parameter_is_conservative():
    sub = "rtsp://camera:554/user=admin&password=x&channel=1&stream=1.sdp"
    assert quality_rtsp_url(sub, "high") == sub
    assert quality_rtsp_url(sub, "ultra") == sub

    main = "rtsp://camera:554/user=admin&password=x&channel=1&stream=0.sdp"
    assert quality_rtsp_url(main, "low").endswith("&stream=1.sdp")
    assert quality_rtsp_url(main, "high") == main
