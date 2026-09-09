from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
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
        self.detector._alert_state["movement"]["last_time"] = 100.0
        self.detector._alert_state["movement"]["backoff_index"] = 3

        self.detector._trigger_alert(self.frame, "movement")

        self.on_alert.assert_called_once()
        self.assertEqual(
            self.detector._alert_state["movement"]["backoff_index"], 1
        )

    @patch("core.detector.cv2.imwrite", return_value=True)
    @patch("core.detector.time.time", return_value=2001.0)
    def test_lighting_and_movement_have_independent_backoff(
        self, _time: Mock, _imwrite: Mock
    ) -> None:
        self.detector._trigger_alert(self.frame, "lighting")
        self.detector._trigger_alert(self.frame, "movement")

        self.assertEqual(self.on_alert.call_count, 2)

    def test_only_dark_to_bright_is_a_lighting_alert(self) -> None:
        dark = np.full((80, 80, 3), 10, dtype=np.uint8)
        bright = np.full((80, 80, 3), 80, dtype=np.uint8)

        self.assertEqual(self.detector._classify_change(dark), (None, False))
        self.assertEqual(self.detector._classify_change(bright), ("lighting", True))
        self.assertEqual(self.detector._classify_change(dark), (None, False))

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

    @patch("core.detector.time.sleep")
    def test_capture_until_stops_when_frame_matches(self, _sleep: Mock) -> None:
        frame = np.full((8, 8, 3), 80, dtype=np.uint8)
        camera = Mock()
        camera.read_frame.side_effect = [frame] * 5
        self.detector._camera = camera
        stop_when = Mock(side_effect=[False, True])

        captured_count = self.detector.capture_until(10, 2, stop_when)

        self.assertEqual(captured_count, 2)
        self.assertEqual(stop_when.call_count, 2)
        self.assertEqual(camera.read_frame.call_count, 5)

    def test_cleanup_removes_only_expired_evidence_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.detector._log_dir = temp_dir
            self.detector._evidence_retention_days = 7
            old_alert = Path(temp_dir, "alert_20260101_000000.jpg")
            old_person = Path(temp_dir, "person_20260101_000000.jpg")
            recent_alert = Path(temp_dir, "alert_20260109_000000.jpg")
            unrelated_image = Path(temp_dir, "manual_photo.jpg")
            log_file = Path(temp_dir, "smartcam.log")

            for path in (
                old_alert,
                old_person,
                recent_alert,
                unrelated_image,
                log_file,
            ):
                path.write_bytes(b"test")

            os.utime(old_alert, (86400, 86400))
            os.utime(old_person, (86400, 86400))
            os.utime(recent_alert, (9 * 86400, 9 * 86400))
            now = 10 * 86400

            removed = self.detector._cleanup_old_evidence(now=now)

            self.assertEqual(removed, 2)
            self.assertFalse(old_alert.exists())
            self.assertFalse(old_person.exists())
            self.assertTrue(recent_alert.exists())
            self.assertTrue(unrelated_image.exists())
            self.assertTrue(log_file.exists())


if __name__ == "__main__":
    unittest.main()
