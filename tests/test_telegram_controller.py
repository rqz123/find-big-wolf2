from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from core.person_detector import PersonScan
from core.telegram_controller import TelegramCameraController


class TelegramCameraControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = Mock()
        self.detector.current_device_index = 1
        self.detector.is_user_paused = False
        self.detector.is_suspended = False
        self.notifier = Mock()
        self.person_detector = Mock()
        self.pause_change = Mock()
        self.controller = TelegramCameraController(
            cfg={
                "schedule": {
                    "start": "09:00",
                    "end": "18:00",
                    "weekdays": [0, 1, 2, 3, 4],
                },
                "telegram": {
                    "clip_duration_sec": 2,
                    "clip_fps": 2,
                    "clip_max_width": 320,
                },
            },
            detector=self.detector,
            notifier=self.notifier,
            on_pause_change=self.pause_change,
            person_detector=self.person_detector,
        )

    def test_pause_keeps_manual_capture_available(self) -> None:
        self.controller.handle("/pause")

        self.detector.user_pause.assert_called_once()
        self.pause_change.assert_called_once_with(True)
        response = self.notifier.send_message.call_args.args[0]
        self.assertIn("/photo", response)
        self.assertIn("/clip", response)

    @patch("core.telegram_controller.is_work_time", return_value=False)
    def test_auto_respects_schedule(self, _is_work_time: Mock) -> None:
        self.controller.handle("/auto")

        self.detector.user_resume.assert_called_once_with(active=False)
        self.pause_change.assert_called_once_with(False)
        response = self.notifier.send_message.call_args.args[0]
        self.assertIn("工作时间", response)

    def test_photo_captures_and_sends_jpeg(self) -> None:
        self.detector.capture_frame.return_value = np.zeros((20, 30, 3), np.uint8)

        def verify_photo(path: str, _caption: str) -> bool:
            self.assertTrue(Path(path).is_file())
            return True

        self.notifier.send_alert.side_effect = verify_photo
        self.controller.handle("/photo")

        self.notifier.send_alert.assert_called_once()

    def test_clip_creates_multi_frame_animation(self) -> None:
        self.detector.capture_frames.return_value = [
            np.full((30, 40, 3), value, np.uint8)
            for value in (0, 50, 100, 150)
        ]

        def verify_animation(path: str, _caption: str) -> bool:
            self.assertTrue(Path(path).is_file())
            with Image.open(path) as animation:
                self.assertGreaterEqual(animation.n_frames, 2)
                self.assertLessEqual(animation.width, 320)
            return True

        self.notifier.send_animation.side_effect = verify_animation
        self.controller.handle("/clip")

        self.notifier.send_animation.assert_called_once()
        self.detector.capture_frames.assert_called_once_with(4, 2.0)

    def test_camera_cycles_to_next_device(self) -> None:
        self.detector.camera_count = 2
        self.detector.cycle_camera.return_value = (1, "Integrated Webcam")

        self.controller.handle("/camera")

        self.detector.cycle_camera.assert_called_once()
        response = self.notifier.send_message.call_args.args[0]
        self.assertIn("Integrated Webcam", response)
        self.assertIn("1", response)

    def test_camera_does_nothing_with_single_device(self) -> None:
        self.detector.camera_count = 1

        self.controller.handle("/camera")

        self.detector.cycle_camera.assert_not_called()
        response = self.notifier.send_message.call_args.args[0]
        self.assertIn("只有一台", response)

    def test_lighting_alert_is_text_only(self) -> None:
        self.controller.send_motion_alert("unused.jpg", "lighting")

        self.notifier.send_message.assert_called_once()
        self.notifier.send_animation.assert_not_called()
        self.detector.capture_frames.assert_not_called()
        self.detector.capture_until.assert_not_called()
        self.assertIn("由暗变亮", self.notifier.send_message.call_args.args[0])

    @patch("core.telegram_controller.cv2.imread")
    def test_movement_with_person_sends_annotated_animation(self, imread: Mock) -> None:
        frame = np.full((30, 40, 3), 80, np.uint8)
        imread.return_value = frame
        self.detector.recent_frames.return_value = [frame]
        self.detector.capture_frames.return_value = [frame, frame]
        self.person_detector.scan.return_value = PersonScan(
            available=True,
            found=True,
            frames=[frame, frame, frame],
            max_confidence=0.82,
        )
        self.notifier.send_animation.return_value = True

        self.controller.send_motion_alert("alert.jpg", "movement")

        self.notifier.send_animation.assert_called_once()
        self.notifier.send_message.assert_not_called()
        self.detector.capture_until.assert_not_called()
        self.assertIn("82%", self.notifier.send_animation.call_args.args[1])

    @patch("core.telegram_controller.cv2.imread")
    def test_movement_tracks_until_person_appears(self, imread: Mock) -> None:
        frame = np.full((30, 40, 3), 80, np.uint8)
        annotated = np.full((30, 40, 3), 100, np.uint8)
        imread.return_value = frame
        self.detector.recent_frames.return_value = [frame]
        self.person_detector.scan.side_effect = [
            PersonScan(True, False, [frame, frame]),
            PersonScan(True, False, [frame]),
            PersonScan(True, True, [annotated], 0.76, annotated),
        ]

        def capture_until(duration: float, fps: float, stop_when) -> list:
            self.assertEqual(duration, 15.0)
            self.assertEqual(fps, 2.0)
            captured = []
            for item in (frame, frame, frame):
                captured.append(item)
                if stop_when(item):
                    break
            return len(captured)

        self.detector.capture_until.side_effect = capture_until
        self.notifier.send_animation.return_value = True

        self.controller.send_motion_alert("alert.jpg", "movement")

        self.assertEqual(self.person_detector.scan.call_count, 3)
        self.notifier.send_animation.assert_called_once()
        self.notifier.send_message.assert_not_called()
        self.assertIn("76%", self.notifier.send_animation.call_args.args[1])

    @patch("core.telegram_controller.cv2.imread")
    def test_movement_without_person_sends_text_only(self, imread: Mock) -> None:
        frame = np.full((30, 40, 3), 80, np.uint8)
        imread.return_value = frame
        self.detector.recent_frames.return_value = [frame]
        self.person_detector.scan.return_value = PersonScan(
            available=True,
            found=False,
            frames=[frame],
        )

        def capture_until(_duration: float, _fps: float, stop_when) -> list:
            frames = [frame, frame]
            for item in frames:
                stop_when(item)
            return len(frames)

        self.detector.capture_until.side_effect = capture_until

        self.controller.send_motion_alert("alert.jpg", "movement")

        self.notifier.send_message.assert_called_once()
        self.notifier.send_animation.assert_not_called()
        self.assertEqual(self.person_detector.scan.call_count, 3)
        self.assertIn("未识别到人", self.notifier.send_message.call_args.args[0])

    @patch("core.telegram_controller.cv2.imread")
    def test_person_detector_failure_fails_open_with_animation(self, imread: Mock) -> None:
        frame = np.full((30, 40, 3), 80, np.uint8)
        imread.return_value = frame
        self.detector.recent_frames.return_value = [frame]
        self.detector.capture_frames.return_value = [frame, frame]
        self.person_detector.scan.return_value = PersonScan(
            available=False,
            found=False,
            frames=[frame, frame, frame],
        )
        self.notifier.send_animation.return_value = True

        self.controller.send_motion_alert("alert.jpg", "movement")

        self.notifier.send_animation.assert_called_once()
        self.assertIn("识别暂不可用", self.notifier.send_animation.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
