"""Telegram Bot API notification module.

Credentials are loaded from ``telegram_credentials.yaml`` in the project root.
That file is intentionally ignored by Git. Environment variables override it:

    SMARTCAM_TELEGRAM_BOT_TOKEN
    SMARTCAM_TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

log = logging.getLogger("smartcam.telegram")

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CREDENTIALS_FILE = "telegram_credentials.yaml"
_PLACEHOLDERS = {"", "REPLACE_ME", "YOUR_BOT_TOKEN", "YOUR_CHAT_ID"}


class TelegramNotifier:
    """Send alert images through the official Telegram Bot API."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        telegram_cfg = cfg.get("telegram", {})
        credentials_file = str(
            telegram_cfg.get("credentials_file", _DEFAULT_CREDENTIALS_FILE)
        ).strip()
        credentials_path = Path(credentials_file)
        if not credentials_path.is_absolute():
            credentials_path = _ROOT / credentials_path

        self._credentials_path = credentials_path
        self._timeout_sec = max(1, int(telegram_cfg.get("timeout_sec", 20)))
        self._retries = max(1, int(telegram_cfg.get("retries", 3)))

        if not self.is_configured:
            log.warning(
                "Telegram is not configured; run Telegram Setup from the tray menu."
            )

    @property
    def credentials_path(self) -> str:
        return str(self._credentials_path)

    @property
    def is_configured(self) -> bool:
        token, chat_id = self._load_credentials()
        return token not in _PLACEHOLDERS and chat_id not in _PLACEHOLDERS

    def ensure_credentials_file(self) -> str:
        """Create an ignored credential template if needed and return its path."""
        if not self._credentials_path.exists():
            self._credentials_path.parent.mkdir(parents=True, exist_ok=True)
            self._credentials_path.write_text(
                "# Keep this file private; it is ignored by Git.\n"
                "bot_token: \"\"\n"
                "chat_id: \"\"\n",
                encoding="utf-8",
            )
            log.info("Created Telegram credentials file: %s", self._credentials_path)
        return str(self._credentials_path)

    def send_alert(self, image_path: str, caption: str = "Motion detected!") -> bool:
        """Send one image alert, retrying temporary network/server failures."""
        return self._send_media(
            method="sendPhoto",
            field_name="photo",
            media_path=image_path,
            caption=caption,
            media_label="alert",
        )

    def send_animation(self, animation_path: str, caption: str = "Camera clip") -> bool:
        """Send a GIF or silent MPEG-4 animation."""
        return self._send_media(
            method="sendAnimation",
            field_name="animation",
            media_path=animation_path,
            caption=caption,
            media_label="animation",
        )

    def send_message(self, text: str) -> bool:
        """Send a text response to the configured private chat."""
        token, chat_id = self._load_credentials()
        if token not in _PLACEHOLDERS and chat_id in _PLACEHOLDERS:
            chat_id = self._discover_chat_id(token)

        if token in _PLACEHOLDERS or chat_id in _PLACEHOLDERS:
            log.error(
                "Telegram message not sent: add bot_token to %s, send /start to the "
                "bot, and try again",
                self._credentials_path,
            )
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        return self._post_form(
            url,
            {"chat_id": chat_id, "text": text[:4096]},
            "message",
        )

    @property
    def authorized_chat_id(self) -> str:
        """Return the only chat allowed to control the camera."""
        _, chat_id = self._load_credentials()
        return "" if chat_id in _PLACEHOLDERS else chat_id

    def get_updates(
        self,
        offset: Optional[int] = None,
        timeout_sec: int = 0,
    ) -> Optional[List[Dict[str, Any]]]:
        """Read Bot API updates. None means unavailable/error; [] means no updates."""
        token, chat_id = self._load_credentials()
        if token in _PLACEHOLDERS or chat_id in _PLACEHOLDERS:
            return None

        poll_timeout = max(0, min(50, int(timeout_sec)))
        params: Dict[str, Any] = {
            "timeout": poll_timeout,
            "limit": 100,
            "allowed_updates": '["message"]',
        }
        if offset is not None:
            params["offset"] = int(offset)

        url = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            response = requests.get(
                url,
                params=params,
                timeout=(5, max(self._timeout_sec, poll_timeout + 5)),
            )
            payload = self._response_payload(response)
            if response.ok and payload.get("ok") is True:
                updates = payload.get("result", [])
                return updates if isinstance(updates, list) else []
            log.error(
                "Telegram getUpdates failed (HTTP %s): %s",
                response.status_code,
                payload.get("description", response.reason),
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            log.warning("Telegram command poll failed (%s)", type(exc).__name__)
        except requests.RequestException as exc:
            log.error("Telegram command poll failed (%s)", type(exc).__name__)
        return None

    def set_commands(self, commands: List[Dict[str, str]]) -> bool:
        """Publish the command menu shown by Telegram clients."""
        token, chat_id = self._load_credentials()
        if token in _PLACEHOLDERS or chat_id in _PLACEHOLDERS:
            return False

        url = f"https://api.telegram.org/bot{token}/setMyCommands"
        try:
            response = requests.post(
                url,
                json={"commands": commands},
                timeout=(5, self._timeout_sec),
            )
            payload = self._response_payload(response)
            if response.ok and payload.get("ok") is True:
                log.info("Telegram command menu registered")
                return True
            log.error(
                "Cannot register Telegram command menu (HTTP %s): %s",
                response.status_code,
                payload.get("description", response.reason),
            )
        except requests.RequestException as exc:
            log.warning("Cannot register Telegram commands (%s)", type(exc).__name__)
        return False

    def _send_media(
        self,
        method: str,
        field_name: str,
        media_path: str,
        caption: str,
        media_label: str,
    ) -> bool:
        token, chat_id = self._load_credentials()
        if token not in _PLACEHOLDERS and chat_id in _PLACEHOLDERS:
            chat_id = self._discover_chat_id(token)

        if token in _PLACEHOLDERS or chat_id in _PLACEHOLDERS:
            log.error(
                "Telegram media not sent: add bot_token to %s, send /start to the "
                "bot, and try again",
                self._credentials_path,
            )
            return False

        path = Path(media_path).resolve()
        if not path.is_file():
            log.error("Telegram media file does not exist: %s", path)
            return False

        url = f"https://api.telegram.org/bot{token}/{method}"
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        for attempt in range(1, self._retries + 1):
            try:
                with path.open("rb") as media_file:
                    response = requests.post(
                        url,
                        data={"chat_id": chat_id, "caption": caption[:1024]},
                        files={field_name: (path.name, media_file, mime_type)},
                        timeout=(5, self._timeout_sec),
                    )

                payload = self._response_payload(response)
                if response.ok and payload.get("ok") is True:
                    log.info("Telegram %s sent successfully", media_label)
                    return True

                description = payload.get("description", response.reason)
                log.error(
                    "Telegram API rejected %s (HTTP %s): %s",
                    media_label,
                    response.status_code,
                    description,
                )

                if response.status_code < 500 and response.status_code != 429:
                    return False

                parameters = payload.get("parameters", {})
                retry_after = (
                    parameters.get("retry_after")
                    if isinstance(parameters, dict)
                    else None
                )
                delay = self._retry_delay(attempt, retry_after)
            except (requests.Timeout, requests.ConnectionError) as exc:
                log.warning(
                    "Telegram %s attempt %s/%s failed (%s)",
                    media_label,
                    attempt,
                    self._retries,
                    type(exc).__name__,
                )
                delay = self._retry_delay(attempt)
            except requests.RequestException as exc:
                log.error("Telegram request failed (%s)", type(exc).__name__)
                return False
            except OSError as exc:
                log.error("Cannot read Telegram media %s: %s", path, exc)
                return False

            if attempt < self._retries:
                time.sleep(delay)

        log.error("Telegram %s failed after %s attempts", media_label, self._retries)
        return False

    def _post_form(self, url: str, data: Dict[str, Any], label: str) -> bool:
        for attempt in range(1, self._retries + 1):
            try:
                response = requests.post(
                    url,
                    data=data,
                    timeout=(5, self._timeout_sec),
                )
                payload = self._response_payload(response)
                if response.ok and payload.get("ok") is True:
                    return True
                log.error(
                    "Telegram API rejected %s (HTTP %s): %s",
                    label,
                    response.status_code,
                    payload.get("description", response.reason),
                )
                if response.status_code < 500 and response.status_code != 429:
                    return False
                parameters = payload.get("parameters", {})
                retry_after = (
                    parameters.get("retry_after")
                    if isinstance(parameters, dict)
                    else None
                )
                delay = self._retry_delay(attempt, retry_after)
            except (requests.Timeout, requests.ConnectionError) as exc:
                log.warning(
                    "Telegram %s attempt %s/%s failed (%s)",
                    label,
                    attempt,
                    self._retries,
                    type(exc).__name__,
                )
                delay = self._retry_delay(attempt)
            except requests.RequestException as exc:
                log.error("Telegram request failed (%s)", type(exc).__name__)
                return False

            if attempt < self._retries:
                time.sleep(delay)

        return False

    def _discover_chat_id(self, token: str) -> str:
        """Find the latest private chat after the user has sent the bot /start."""
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        try:
            response = requests.get(url, timeout=(5, self._timeout_sec))
            payload = self._response_payload(response)
            if not response.ok or payload.get("ok") is not True:
                description = payload.get("description", response.reason)
                log.error("Cannot discover Telegram chat: %s", description)
                return ""

            updates = payload.get("result", [])
            private_chat_ids = []
            for update in reversed(updates if isinstance(updates, list) else []):
                message = update.get("message", {}) if isinstance(update, dict) else {}
                chat = message.get("chat", {}) if isinstance(message, dict) else {}
                if chat.get("type") == "private" and chat.get("id") is not None:
                    candidate = str(chat["id"])
                    if candidate not in private_chat_ids:
                        private_chat_ids.append(candidate)

            if len(private_chat_ids) == 1:
                chat_id = private_chat_ids[0]
                self._save_discovered_chat_id(chat_id)
                log.info("Telegram private chat discovered and saved")
                return chat_id

            if len(private_chat_ids) > 1:
                log.error(
                    "Multiple Telegram private chats found; set chat_id explicitly "
                    "to avoid sending camera images to the wrong person"
                )
                return ""

            log.error("No Telegram private chat found; send /start to the bot first")
        except requests.RequestException as exc:
            log.error("Cannot discover Telegram chat (%s)", type(exc).__name__)
        return ""

    def _save_discovered_chat_id(self, chat_id: str) -> None:
        values: Dict[str, Any] = {}
        if self._credentials_path.is_file():
            try:
                with self._credentials_path.open("r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle) or {}
                if isinstance(loaded, dict):
                    values = loaded
            except (OSError, yaml.YAMLError) as exc:
                log.error("Cannot update Telegram credentials: %s", exc)
                return

        values["chat_id"] = chat_id
        try:
            self._credentials_path.parent.mkdir(parents=True, exist_ok=True)
            with self._credentials_path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(values, handle, allow_unicode=True, sort_keys=False)
        except OSError as exc:
            log.error("Cannot save discovered Telegram chat ID: %s", exc)

    def _load_credentials(self) -> Tuple[str, str]:
        values: Dict[str, Any] = {}
        if self._credentials_path.is_file():
            try:
                with self._credentials_path.open("r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle) or {}
                if isinstance(loaded, dict):
                    values = loaded
                else:
                    log.error(
                        "Telegram credentials must be a YAML mapping: %s",
                        self._credentials_path,
                    )
            except (OSError, yaml.YAMLError) as exc:
                log.error("Cannot load Telegram credentials: %s", exc)

        token = os.getenv(
            "SMARTCAM_TELEGRAM_BOT_TOKEN", str(values.get("bot_token", ""))
        ).strip()
        chat_id = os.getenv(
            "SMARTCAM_TELEGRAM_CHAT_ID", str(values.get("chat_id", ""))
        ).strip()
        return token, chat_id

    @staticmethod
    def _response_payload(response: requests.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except ValueError:
            return {}

    @staticmethod
    def _retry_delay(attempt: int, retry_after: Any = None) -> float:
        try:
            if retry_after is not None:
                return min(30.0, max(1.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
        return min(8.0, float(2 ** (attempt - 1)))
