from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from notify.telegram import TelegramNotifier


class TelegramNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.credentials_path = root / "telegram_credentials.yaml"
        self.image_path = root / "alert.jpg"
        self.image_path.write_bytes(b"fake jpeg")
        self.cfg = {
            "telegram": {
                "credentials_file": str(self.credentials_path),
                "timeout_sec": 2,
                "retries": 3,
            }
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_credentials(self, token: str, chat_id: str = "") -> None:
        self.credentials_path.write_text(
            yaml.safe_dump({"bot_token": token, "chat_id": chat_id}),
            encoding="utf-8",
        )

    @staticmethod
    def _response(payload: dict, status_code: int = 200) -> Mock:
        response = Mock()
        response.ok = 200 <= status_code < 400
        response.status_code = status_code
        response.reason = "OK" if response.ok else "Error"
        response.json.return_value = payload
        return response

    @patch("notify.telegram.requests.post")
    def test_sends_photo_with_caption(self, post: Mock) -> None:
        self._write_credentials("123:secret", "456")
        post.return_value = self._response({"ok": True, "result": {}})

        notifier = TelegramNotifier(self.cfg)
        sent = notifier.send_alert(str(self.image_path), "camera alert")

        self.assertTrue(sent)
        post.assert_called_once()
        request = post.call_args
        self.assertEqual(request.kwargs["data"]["chat_id"], "456")
        self.assertEqual(request.kwargs["data"]["caption"], "camera alert")
        self.assertIn("photo", request.kwargs["files"])

    @patch("notify.telegram.requests.post")
    @patch("notify.telegram.requests.get")
    def test_discovers_single_private_chat(self, get: Mock, post: Mock) -> None:
        self._write_credentials("123:secret")
        get.return_value = self._response(
            {
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 456, "type": "private"}}}
                ],
            }
        )
        post.return_value = self._response({"ok": True, "result": {}})

        notifier = TelegramNotifier(self.cfg)
        self.assertTrue(notifier.send_alert(str(self.image_path)))

        saved = yaml.safe_load(self.credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(str(saved["chat_id"]), "456")
        post.assert_called_once()

    @patch("notify.telegram.requests.post")
    @patch("notify.telegram.requests.get")
    def test_refuses_ambiguous_private_chats(self, get: Mock, post: Mock) -> None:
        self._write_credentials("123:secret")
        get.return_value = self._response(
            {
                "ok": True,
                "result": [
                    {"message": {"chat": {"id": 456, "type": "private"}}},
                    {"message": {"chat": {"id": 789, "type": "private"}}},
                ],
            }
        )

        notifier = TelegramNotifier(self.cfg)
        self.assertFalse(notifier.send_alert(str(self.image_path)))
        post.assert_not_called()

    @patch("notify.telegram.requests.post")
    def test_does_not_retry_authentication_error(self, post: Mock) -> None:
        self._write_credentials("123:bad", "456")
        post.return_value = self._response(
            {"ok": False, "description": "Unauthorized"}, status_code=401
        )

        notifier = TelegramNotifier(self.cfg)
        self.assertFalse(notifier.send_alert(str(self.image_path)))
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
