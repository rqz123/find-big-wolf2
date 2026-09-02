from __future__ import annotations

import unittest
from unittest.mock import Mock

from notify.telegram_commands import TelegramCommandListener


class TelegramCommandListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.notifier = Mock()
        self.notifier.authorized_chat_id = "456"
        self.handler = Mock()
        self.listener = TelegramCommandListener(self.notifier, self.handler)

    def test_accepts_command_only_from_configured_private_chat(self) -> None:
        self.listener._process_update(
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 456, "type": "private"},
                    "text": "/photo@SmartCamBot",
                },
            }
        )
        self.handler.assert_called_once_with("/photo")

    def test_ignores_unauthorized_chat(self) -> None:
        self.listener._process_update(
            {
                "update_id": 11,
                "message": {
                    "chat": {"id": 999, "type": "private"},
                    "text": "/photo",
                },
            }
        )
        self.handler.assert_not_called()

    def test_plain_text_shows_help(self) -> None:
        self.listener._process_update(
            {
                "update_id": 12,
                "message": {
                    "chat": {"id": 456, "type": "private"},
                    "text": "hello",
                },
            }
        )
        self.handler.assert_called_once_with("/help")

    def test_offset_advances_past_latest_update(self) -> None:
        self.assertEqual(
            TelegramCommandListener._next_offset(
                [{"update_id": 7}, {"update_id": 11}], 5
            ),
            12,
        )


if __name__ == "__main__":
    unittest.main()
