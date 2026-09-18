from core.motion import MotionDetector


def test_motion_detector_debounces_real_change():
    detector = MotionDetector(
        lambda: None,
        lambda active, frame: None,
        0.5,
        threshold=6,
        min_changed_fraction=0.008,
    )

    quiet = detector._decode(_jpeg(20))
    changed = detector._decode(_jpeg(240))

    assert detector._compare(quiet) is False
    assert detector._compare(changed) is False
    assert detector._compare(changed) is True
    assert detector.active is True

    assert detector._compare(quiet) is True
    assert detector._compare(quiet) is True
    assert detector._compare(quiet) is False


def test_motion_detector_reports_metrics():
    detector = MotionDetector(lambda: None, lambda active, frame: None, 0.5, 6, 0.008)
    frame = detector._decode(_jpeg(80))
    detector._compare(frame)
    diagnostics = detector.diagnostics()
    assert 'mean_difference' in diagnostics
    assert 'changed_fraction' in diagnostics
    assert diagnostics['threshold'] == 6
    assert diagnostics['min_changed_fraction'] == 0.008


def _jpeg(value: int) -> bytes:
    from io import BytesIO
    from PIL import Image

    image = Image.new('L', (320, 180), value)
    out = BytesIO()
    image.save(out, format='JPEG', quality=95)
    return out.getvalue()



def test_motion_camera_selection():
    from core.nvr import motion_selected_for_camera

    assert motion_selected_for_camera('camera-1', {'enabled': False}, ['camera-1']) is False
    assert motion_selected_for_camera('camera-1', {'enabled': True}, []) is True
    assert motion_selected_for_camera('camera-1', {'enabled': True}, ['camera-1']) is True
    assert motion_selected_for_camera('camera-2', {'enabled': True}, ['camera-1']) is False
