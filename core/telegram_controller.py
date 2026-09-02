"""SmartCam actions exposed through authorized Telegram bot commands."""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np
from PIL import Image

from core.detector import MotionDetector
from core.scheduler import is_work_time
from notify.telegram import TelegramNotifier

log = logging.getLogger("smartcam.telegram.controller")

_HELP_TEXT = """SmartCam Watcher 命令
/photo - 立即拍摄一张照片
/clip - 拍摄几秒低帧率动态画面
/auto - 启用按时间表自动检测
/pause - 暂停自动检测（仍可手动取图）
/status - 查看当前状态
/help - 显示本帮助"""


class TelegramCameraController:
    """Translate Telegram commands into safe camera and detector operations."""

    def __init__(
        self,
        cfg: dict,
        detector: MotionDetector,
        notifier: TelegramNotifier,
        on_pause_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._cfg = cfg
        self._detector = detector
        self._notifier = notifier
        self._on_pause_change = on_pause_change or (lambda _paused: None)

        telegram_cfg = cfg.get("telegram", {})
        self._clip_duration_sec = max(
            1.0, min(10.0, float(telegram_cfg.get("clip_duration_sec", 5)))
        )
        self._clip_fps = max(
            0.5, min(5.0, float(telegram_cfg.get("clip_fps", 2)))
        )
        self._clip_max_width = max(
            160, min(1280, int(telegram_cfg.get("clip_max_width", 640)))
        )

    def handle(self, command: str) -> None:
        handlers = {
            "/start": self._help,
            "/help": self._help,
            "/photo": self._photo,
            "/snapshot": self._photo,
            "/clip": self._clip,
            "/auto": self._auto,
            "/resume": self._auto,
            "/pause": self._pause,
            "/status": self._status,
        }
        handler = handlers.get(command)
        if handler is None:
            self._notifier.send_message("未知命令。\n\n" + _HELP_TEXT)
            return
        handler()

    def send_motion_alert(self, image_path: str, event_type: str) -> None:
        """Turn an automatic event into a short animation, with photo fallback."""
        first_frame = cv2.imread(image_path)
        frames = [first_frame] if first_frame is not None else []
        frames.extend(self._capture_remaining_frames(len(frames)))

        event_label = "光线变化" if event_type == "lighting" else "检测到移动"
        caption = f"SmartCam：{event_label}\n{self._timestamp()}"
        if len(frames) >= 2 and self._send_frames(frames, caption):
            return

        log.warning("Animation unavailable; falling back to the alert photo")
        self._notifier.send_alert(image_path, caption)

    def _help(self) -> None:
        self._notifier.send_message(_HELP_TEXT)

    def _photo(self) -> None:
        frame = self._detector.capture_frame()
        if frame is None:
            self._notifier.send_message("取图失败：摄像头当前不可用。")
            return

        with tempfile.TemporaryDirectory(prefix="smartcam-photo-") as temp_dir:
            photo_path = Path(temp_dir) / "smartcam_photo.jpg"
            if not cv2.imwrite(str(photo_path), frame):
                self._notifier.send_message("取图失败：无法生成图片。")
                return
            self._notifier.send_alert(
                str(photo_path),
                f"SmartCam 即时取图\n{self._timestamp()}",
            )

    def _clip(self) -> None:
        self._notifier.send_message(
            f"正在拍摄 {self._clip_duration_sec:g} 秒动态画面…"
        )
        frames = self._detector.capture_frames(self._frame_count, self._clip_fps)
        if len(frames) < 2:
            self._notifier.send_message("动态画面拍摄失败：摄像头当前不可用。")
            return
        if not self._send_frames(
            frames,
            f"SmartCam 动态画面\n{self._timestamp()}",
        ):
            self._notifier.send_message("动态画面发送失败，请查看电脑端日志。")

    def _auto(self) -> None:
        active_now = is_work_time(self._cfg)
        self._detector.user_resume(active=active_now)
        self._on_pause_change(False)
        if active_now:
            message = "自动检测已启用，当前正在监控。"
        else:
            message = "自动检测已启用；当前不在设定工作时间，处于待机状态。"
        self._notifier.send_message(message)

    def _pause(self) -> None:
        self._detector.user_pause()
        self._on_pause_change(True)
        self._notifier.send_message(
            "自动检测已暂停。你仍可使用 /photo 或 /clip 手动取图。"
        )

    def _status(self) -> None:
        if self._detector.is_user_paused:
            mode = "已手动暂停（命令取图可用）"
        elif self._detector.is_suspended:
            mode = "自动模式：当前在工作时间外待机"
        else:
            mode = "自动模式：监控中"
        self._notifier.send_message(
            "SmartCam 状态\n"
            f"模式：{mode}\n"
            f"摄像头索引：{self._detector.current_device_index}\n"
            f"时间：{self._timestamp()}"
        )

    @property
    def _frame_count(self) -> int:
        return max(2, int(round(self._clip_duration_sec * self._clip_fps)))

    def _capture_remaining_frames(self, existing_count: int) -> List[np.ndarray]:
        remaining = max(0, self._frame_count - existing_count)
        if remaining == 0:
            return []
        return self._detector.capture_frames(remaining, self._clip_fps)

    def _send_frames(self, frames: List[np.ndarray], caption: str) -> bool:
        with tempfile.TemporaryDirectory(prefix="smartcam-clip-") as temp_dir:
            animation_path = Path(temp_dir) / "smartcam_clip.gif"
            try:
                self._write_gif(frames, animation_path)
            except (OSError, ValueError) as exc:
                log.error("Cannot create camera animation (%s)", type(exc).__name__)
                return False
            return self._notifier.send_animation(str(animation_path), caption)

    def _write_gif(self, frames: List[np.ndarray], output_path: Path) -> None:
        images: List[Image.Image] = []
        for frame in frames:
            if frame is None or frame.size == 0:
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            if image.width > self._clip_max_width:
                height = max(1, round(image.height * self._clip_max_width / image.width))
                image = image.resize(
                    (self._clip_max_width, height),
                    Image.Resampling.LANCZOS,
                )
            images.append(
                image.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
            )

        if len(images) < 2:
            raise ValueError("At least two frames are required for an animation")

        frame_duration_ms = max(100, round(1000 / self._clip_fps))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(
            output_path,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=frame_duration_ms,
            loop=0,
            optimize=False,
            disposal=2,
        )

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
