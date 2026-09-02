from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from core.telegram_controller import TelegramCameraController


class TelegramCameraControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = Mock()
        self.detector.current_device_index = 1
        self.detector.is_user_paused = False
        self.detector.is_suspended = False
        self.notifier = Mock()
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


if __name__ == "__main__":
    unittest.main()
