from core.rtsp import with_credentials


def test_rtsp_credentials_are_escaped() -> None:
    url = with_credentials('rtsp://192.168.0.1:554/live/ch00_0', 'admin', 'p@ss:word')
    assert url == 'rtsp://admin:p%40ss%3Aword@192.168.0.1:554/live/ch00_0'
