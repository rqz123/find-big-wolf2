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


if __name__ == "__main__":
    unittest.main()
