"""Authorized Telegram command polling for SmartCam Watcher."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from notify.telegram import TelegramNotifier

log = logging.getLogger("smartcam.telegram.commands")

BOT_COMMANDS: List[Dict[str, str]] = [
    {"command": "photo", "description": "立即拍摄一张照片"},
    {"command": "clip", "description": "拍摄几秒低帧率动态画面"},
    {"command": "auto", "description": "启用自动检测"},
    {"command": "pause", "description": "暂停自动检测"},
    {"command": "status", "description": "查看摄像头状态"},
    {"command": "help", "description": "显示命令帮助"},
]


class TelegramCommandListener:
    """Receive commands with getUpdates and allow only the configured chat."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        on_command: Callable[[str], None],
        poll_timeout_sec: int = 20,
    ) -> None:
        self._notifier = notifier
        self._on_command = on_command
        self._poll_timeout_sec = max(1, min(50, int(poll_timeout_sec)))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._offset: Optional[int] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="telegram-commands",
            daemon=True,
        )
        self._thread.start()
        log.info("Telegram command listener started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_timeout_sec + 6)
        log.info("Telegram command listener stopped")

    def _loop(self) -> None:
        initialized = False
        while not self._stop_event.is_set():
            if not self._notifier.is_configured:
                initialized = False
                self._offset = None
                self._stop_event.wait(timeout=5)
                continue

            if not initialized:
                # Confirm and discard commands queued before this program start.
                pending = self._notifier.get_updates(timeout_sec=0)
                if pending is None:
                    self._stop_event.wait(timeout=5)
                    continue
                self._offset = self._next_offset(pending, self._offset)
                self._notifier.set_commands(BOT_COMMANDS)
                initialized = True
                log.info("Telegram command listener ready")

            updates = self._notifier.get_updates(
                offset=self._offset,
                timeout_sec=self._poll_timeout_sec,
            )
            if updates is None:
                self._stop_event.wait(timeout=3)
                continue

            for update in updates:
                self._offset = self._next_offset([update], self._offset)
                self._process_update(update)

    @staticmethod
    def _next_offset(
        updates: List[Dict[str, Any]],
        current: Optional[int],
    ) -> Optional[int]:
        result = current
        for update in updates:
            update_id = update.get("update_id") if isinstance(update, dict) else None
            if isinstance(update_id, int):
                candidate = update_id + 1
                result = candidate if result is None else max(result, candidate)
        return result

    def _process_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message", {}) if isinstance(update, dict) else {}
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        text = message.get("text", "") if isinstance(message, dict) else ""
        chat_id = str(chat.get("id", "")) if isinstance(chat, dict) else ""

        if chat.get("type") != "private" or chat_id != self._notifier.authorized_chat_id:
            if text:
                log.warning("Ignored Telegram command from an unauthorized chat")
            return

        if not isinstance(text, str) or not text.strip():
            return

        first_token = text.strip().split(maxsplit=1)[0].lower()
        command = first_token.split("@", 1)[0]
        if not command.startswith("/"):
            command = "/help"

        try:
            self._on_command(command)
        except Exception as exc:
            log.exception("Telegram command handler failed: %s", type(exc).__name__)
            self._notifier.send_message("命令执行失败，请查看电脑端日志。")
