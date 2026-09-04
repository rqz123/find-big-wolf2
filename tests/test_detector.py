from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np

from core.detector import MotionDetector


class MotionDetectorAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.on_alert = Mock()
        self.detector = MotionDetector(
            cfg={
                "camera": {"device_index": 0},
                "detection": {
                    "backoff_intervals_sec": [300, 900, 1800, 3600],
                    "quiet_reset_sec": 1800,
                },
            },
            camera_list=[(0, "Test camera")],
            on_alert=self.on_alert,
            on_camera_error=Mock(),
            on_status_change=Mock(),
        )
        self.frame = np.zeros((8, 8, 3), dtype=np.uint8)

    @patch("core.detector.cv2.imwrite", return_value=True)
    @patch("core.detector.time.time", return_value=2001.0)
    def test_quiet_period_resets_backoff_before_debounce(
        self, _time: Mock, _imwrite: Mock
    ) -> None:
        self.detector._last_alert_time = 100.0
        self.detector._backoff_index = 3

        self.detector._trigger_alert(self.frame, "movement")

        self.on_alert.assert_called_once()
        self.assertEqual(self.detector._backoff_index, 1)

    @patch("core.detector.time.sleep")
    def test_command_capture_discards_black_warmup_frames(
        self, _sleep: Mock
    ) -> None:
        black = np.zeros((8, 8, 3), dtype=np.uint8)
        good = np.full((8, 8, 3), 80, dtype=np.uint8)
        camera = Mock()
        camera.read_frame.side_effect = [black, black, good, good]
        self.detector._camera = camera

        frame = self.detector.capture_frame()

        self.assertIsNotNone(frame)
        self.assertEqual(float(frame.mean()), 80.0)
        camera.reopen.assert_not_called()

    @patch("core.detector.time.sleep")
    def test_command_capture_reopens_camera_after_only_black_frames(
        self, _sleep: Mock
    ) -> None:
        black = np.zeros((8, 8, 3), dtype=np.uint8)
        good = np.full((8, 8, 3), 90, dtype=np.uint8)
        camera = Mock()
        camera.read_frame.side_effect = [black] * 4 + [black, good] + [good] * 6
        camera.reopen.return_value = True
        self.detector._camera = camera

        frame = self.detector.capture_frame()

        self.assertIsNotNone(frame)
        self.assertEqual(float(frame.mean()), 90.0)
        camera.reopen.assert_called_once()

    def test_cycle_camera_wraps_through_enumerated_list(self) -> None:
        self.detector._camera_list = [(0, "Front"), (2, "Rear")]
        self.detector._camera = Mock(device_index=2)
        self.detector.switch_camera = Mock(return_value=True)

        selected = self.detector.cycle_camera()

        self.assertEqual(selected, (0, "Front"))
        self.detector.switch_camera.assert_called_once_with(0)

    def test_cycle_camera_does_nothing_with_one_camera(self) -> None:
        self.detector.switch_camera = Mock(return_value=True)

        self.assertIsNone(self.detector.cycle_camera())
        self.detector.switch_camera.assert_not_called()


if __name__ == "__main__":
    unittest.main()
