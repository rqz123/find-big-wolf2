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
from core.person_detector import PersonDetector, PersonScan
from core.scheduler import is_work_time
from notify.telegram import TelegramNotifier

log = logging.getLogger("smartcam.telegram.controller")

_HELP_TEXT = """SmartCam Watcher 命令
/photo - 立即拍摄一张照片
/clip - 拍摄几秒低帧率动态画面
/camera - 循环切换到下一台摄像头
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
        person_detector: Optional[PersonDetector] = None,
    ) -> None:
        self._cfg = cfg
        self._detector = detector
        self._notifier = notifier
        self._on_pause_change = on_pause_change or (lambda _paused: None)
        self._person_detector = (
            person_detector if person_detector is not None else PersonDetector(cfg)
        )

        telegram_cfg = cfg.get("telegram", {})
        detection_cfg = cfg.get("detection", {})
        self._clip_duration_sec = max(
            1.0, min(10.0, float(telegram_cfg.get("clip_duration_sec", 5)))
        )
        self._clip_fps = max(
            0.5, min(5.0, float(telegram_cfg.get("clip_fps", 2)))
        )
        self._clip_max_width = max(
            160, min(1280, int(telegram_cfg.get("clip_max_width", 640)))
        )
        self._pre_event_buffer_sec = max(
            0.0, min(10.0, float(detection_cfg.get("pre_event_buffer_sec", 3)))
        )
        self._person_tracking_duration_sec = max(
            1.0,
            min(
                60.0,
                float(detection_cfg.get("person_tracking_duration_sec", 15)),
            ),
        )

    def handle(self, command: str) -> None:
        handlers = {
            "/start": self._help,
            "/help": self._help,
            "/photo": self._photo,
            "/snapshot": self._photo,
            "/clip": self._clip,
            "/camera": self._camera,
            "/switch": self._camera,
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
        """Route light changes to text and confirmed people to an evidence GIF."""
        if event_type == "lighting":
            self._notifier.send_message(
                f"SmartCam：房间由暗变亮，请注意可能有人进入。\n{self._timestamp()}"
            )
            return

        pre_event_count = max(
            1, int(round(self._pre_event_buffer_sec * self._clip_fps))
        )
        frames = self._detector.recent_frames(
            self._pre_event_buffer_sec, pre_event_count
        )
        first_frame = cv2.imread(image_path)
        if MotionDetector.is_frame_usable(first_frame):
            frames.append(first_frame)

        scan = self._person_detector.scan(frames)
        if scan.available and not scan.found:
            scan = self._track_for_person(scan)
        elif not scan.available:
            # Without local inference, keep the existing fail-open behaviour and
            # send a short evidence clip for manual review.
            scan.frames.extend(self._capture_post_frames())

        if scan.available and not scan.found:
            self._notifier.send_message(
                "SmartCam：检测到移动，但在 "
                f"{self._person_tracking_duration_sec:g} 秒跟踪中未识别到人。\n"
                f"{self._timestamp()}"
            )
            return

        fallback_path = image_path
        if scan.available:
            if scan.best_frame is not None:
                alert_path = Path(image_path)
                evidence_path = alert_path.with_name(
                    alert_path.name.replace("alert_", "person_", 1)
                )
                if cv2.imwrite(str(evidence_path), scan.best_frame):
                    log.info("Best person evidence saved: %s", evidence_path)
                    fallback_path = str(evidence_path)
            caption = (
                f"SmartCam：检测到人（最高置信度 {scan.max_confidence:.0%}）\n"
                f"{self._timestamp()}"
            )
        else:
            caption = (
                "SmartCam：检测到移动；人物识别暂不可用，已发送画面供确认。\n"
                f"{self._timestamp()}"
            )

        if len(scan.frames) >= 2 and self._send_frames(scan.frames, caption):
            return

        log.warning("Animation unavailable; falling back to the alert photo")
        self._notifier.send_alert(fallback_path, caption)

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

    def _camera(self) -> None:
        if self._detector.camera_count <= 1:
            self._notifier.send_message("当前只有一台摄像头，保持不变。")
            return

        selected = self._detector.cycle_camera()
        if selected is None:
            self._notifier.send_message("摄像头切换失败，请查看电脑端日志。")
            return

        device_index, name = selected
        self._notifier.send_message(
            f"已切换摄像头：{name}\n设备索引：{device_index}"
        )

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

    def _capture_post_frames(self) -> List[np.ndarray]:
        return self._detector.capture_frames(self._frame_count, self._clip_fps)

    def _track_for_person(self, initial_scan: PersonScan) -> PersonScan:
        """Inspect new frames as they arrive and stop at the first confirmed person."""
        log.info(
            "No person in initial evidence; tracking for up to %s seconds at %s FPS",
            self._person_tracking_duration_sec,
            self._clip_fps,
        )
        evidence_frames = list(initial_scan.frames)
        available = True
        found = False
        max_confidence = initial_scan.max_confidence
        best_frame = initial_scan.best_frame

        def inspect(frame: np.ndarray) -> bool:
            nonlocal available, found, max_confidence, best_frame
            frame_scan = self._person_detector.scan([frame])
            evidence_frames.extend(frame_scan.frames)
            if not frame_scan.available:
                available = False
                return True
            if frame_scan.found:
                found = True
                max_confidence = max(max_confidence, frame_scan.max_confidence)
                if frame_scan.best_frame is not None:
                    best_frame = frame_scan.best_frame
                return True
            return False

        captured_count = self._detector.capture_until(
            self._person_tracking_duration_sec,
            self._clip_fps,
            inspect,
        )
        if found:
            log.info("Person confirmed during tracking after %s frame(s)", captured_count)
        elif not available:
            log.warning("Person tracking stopped because inference became unavailable")
        else:
            log.info("Person tracking ended after %s frame(s) without a match", captured_count)
        return PersonScan(
            available=available,
            found=found,
            frames=evidence_frames,
            max_confidence=max_confidence,
            best_frame=best_frame,
        )

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
            if not MotionDetector.is_frame_usable(frame):
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
