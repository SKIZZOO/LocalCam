from core.nvr import quality_rtsp_url


def test_xmeye_live_stream_quality_switches_main_and_substream():
    url = "rtsp://192.168.0.45:554/live/ch00_1"
    assert quality_rtsp_url(url, "low") == url
    assert quality_rtsp_url(url, "medium") == "rtsp://192.168.0.45:554/live/ch00_0"
    assert quality_rtsp_url(url, "high") == "rtsp://192.168.0.45:554/live/ch00_0"
    assert quality_rtsp_url(url, "ultra") == "rtsp://192.168.0.45:554/live/ch00_0"


def test_v380_stream_parameter_switches():
    url = "rtsp://camera:554/user=admin&password=x&channel=1&stream=1.sdp"
    assert quality_rtsp_url(url, "low") == url
    assert quality_rtsp_url(url, "high").endswith("&stream=0.sdp")
