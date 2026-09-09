"""
Frame-difference motion detector with dual-mode state machine.

Mode A (power-save): reads one frame every poll_interval_sec seconds.
Mode B (high-sensitivity): triggered by motion, reads at fps_high;
                           reverts to Mode A after _MODE_B_IDLE_TIMEOUT seconds of no motion.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from core.camera import CameraCapture
from utils.config_loader import save_config

log = logging.getLogger("smartcam.detector")

_MODE_B_IDLE_TIMEOUT = 10.0   # seconds of no motion before reverting to Mode A
_COMMAND_WARMUP_FRAMES = 4
_COMMAND_REOPEN_WARMUP_FRAMES = 8
_COMMAND_WARMUP_DELAY = 0.12


class MotionDetector:
    """
    Motion detection engine running in a background thread.

    Parameters
    ----------
    cfg : dict
        Full config dict loaded from config.yaml.
    camera_list : list of (device_index, name)
        Available cameras enumerated at startup.  Stored as-is;
        switch_camera() reuses this list instead of re-enumerating
        (re-enumerating while the detector holds a camera causes the
        open() call to fail and the list to shrink).
    on_alert : Callable[[str], None]
        Called with the snapshot file path when motion is detected.
    on_camera_error : Callable
        Called when the camera fails repeatedly (signals tray to go red).
    on_status_change : Callable[[str], None]
        Called with 'active' | 'suspended' | 'error'.
    """

    def __init__(
        self,
        cfg: dict,
        camera_list: List[Tuple[int, str]],
        on_alert: Callable[[str], None],
        on_camera_error: Callable,
        on_status_change: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._camera_list = list(camera_list)   # stable copy, never re-enumerated
        self._on_alert = on_alert
        self._on_camera_error = on_camera_error
        self._on_status_change = on_status_change
        self._preview_update: Optional[Callable[[np.ndarray], None]] = None

        cam_cfg = cfg.get("camera", {})
        det_cfg = cfg.get("detection", {})

        self._camera = CameraCapture(
            device_index=cam_cfg.get("device_index", 0),
            on_error=self._handle_camera_error,
        )
        self._poll_interval: float = cam_cfg.get("poll_interval_sec", 30)
        self._fps_high: float = cam_cfg.get("fps_high", 15)
        self._pixel_threshold: int = det_cfg.get("pixel_threshold", 40)
        self._contour_min_area: int = det_cfg.get("contour_min_area", 1500)
        self._brightness_threshold: float = det_cfg.get("brightness_change_threshold", 25)
        self._backoff_intervals: list = det_cfg.get("backoff_intervals_sec", [300, 900, 1800, 3600])
        self._quiet_reset_sec: float = det_cfg.get("quiet_reset_sec", 1800)
        self._alert_state = {
            "lighting": {"last_time": 0.0, "backoff_index": 0},
            "movement": {"last_time": 0.0, "backoff_index": 0},
        }

        self._evidence_fps = max(0.5, float(det_cfg.get("evidence_fps", 2)))
        self._evidence_buffer_sec = max(
            1.0, float(det_cfg.get("pre_event_buffer_sec", 3))
        )
        self._evidence_retention_days = max(
            1.0, float(det_cfg.get("evidence_retention_days", 7))
        )
        self._evidence_cleanup_interval = max(
            3600.0,
            float(det_cfg.get("evidence_cleanup_interval_hours", 6)) * 3600.0,
        )
        self._next_evidence_cleanup_at = 0.0
        evidence_capacity = max(
            2, ceil(self._evidence_fps * self._evidence_buffer_sec) + 1
        )
        self._recent_frames = deque(maxlen=evidence_capacity)
        self._recent_frames_lock = threading.Lock()
        self._last_evidence_frame_at = 0.0
        self._invalid_monitor_frames = 0

        self._mode: str = "A"
        self._suspended: bool = False
        self._user_paused: bool = False
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._last_alert_time: float = 0.0
        self._last_motion_time: float = 0.0

        self._suspend_event = threading.Event()
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()

        self._log_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs"
        )
        os.makedirs(self._log_dir, exist_ok=True)

        self._on_camera_switched: Optional[Callable] = None
        self._switch_lock = threading.Lock()
        self._command_capture_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public control
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._cleanup_old_evidence()
        self._next_evidence_cleanup_at = (
            time.monotonic() + self._evidence_cleanup_interval
        )
        if not self._camera.open():
            self._on_status_change("error")
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="detector", daemon=True)
        self._thread.start()
        log.info("MotionDetector started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        self._wakeup_event.set()
        self._suspend_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._camera.close()
        log.info("MotionDetector stopped")

    def suspend(self) -> None:
        if not self._suspended:
            self._suspended = True
            self._mode = "A"
            self._on_status_change("suspended")
            log.info("Detector suspended (outside work hours)")

    def resume(self) -> None:
        if self._user_paused:
            log.debug("Scheduler resume ignored — user has manually paused")
            return
        if self._suspended:
            self._suspended = False
            self._on_status_change("active")
            self._suspend_event.set()
            log.info("Detector resumed (work hours)")

    def user_pause(self) -> None:
        self._user_paused = True
        if not self._suspended:
            self._suspended = True
            self._mode = "A"
            self._on_status_change("suspended")
            log.info("Detector paused by user")

    def user_resume(self, active: bool = True) -> None:
        self._user_paused = False
        if active and self._suspended:
            self._suspended = False
            self._on_status_change("active")
            self._suspend_event.set()
            log.info("Detector resumed by user")
        elif not active:
            self.suspend()

    @property
    def is_user_paused(self) -> bool:
        return self._user_paused

    @property
    def is_suspended(self) -> bool:
        return self._suspended

    @property
    def current_device_index(self) -> int:
        return self._camera.device_index

    def capture_frame(self) -> Optional[np.ndarray]:
        """Read a warmed, non-black frame for an on-demand Telegram command."""
        with self._command_capture_lock:
            return self._capture_warmed_frame()

    def capture_frames(self, frame_count: int, fps: float) -> List[np.ndarray]:
        """Capture a short, low-frame-rate sequence without changing monitor mode."""
        count = max(1, int(frame_count))
        interval = 1.0 / max(0.2, float(fps))

        with self._command_capture_lock:
            first_frame = self._capture_warmed_frame()
            if first_frame is None:
                return []

            frames: List[np.ndarray] = [first_frame]
            next_frame_at = time.monotonic()
            for _ in range(count - 1):
                next_frame_at += interval
                remaining = next_frame_at - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                frame = self._camera.read_frame()
                if self.is_frame_usable(frame):
                    frames.append(frame.copy())

        return frames

    def capture_until(
        self,
        duration_sec: float,
        fps: float,
        stop_when: Callable[[np.ndarray], bool],
    ) -> int:
        """Capture for a bounded period, stopping as soon as a frame matches."""
        duration = max(0.1, float(duration_sec))
        interval = 1.0 / max(0.2, float(fps))

        with self._command_capture_lock:
            first_frame = self._capture_warmed_frame()
            if first_frame is None:
                return 0

            captured_count = 0
            started_at = time.monotonic()
            deadline = started_at + duration
            next_frame_at = started_at
            frame: Optional[np.ndarray] = first_frame

            while not self._stop_event.is_set():
                if self.is_frame_usable(frame):
                    captured = frame.copy()
                    captured_count += 1
                    if stop_when(captured):
                        break

                next_frame_at += interval
                if next_frame_at > deadline:
                    break
                remaining = next_frame_at - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                frame = self._camera.read_frame()

        return captured_count

    def recent_frames(self, seconds: float, max_count: int) -> List[np.ndarray]:
        """Return a copy of the low-rate rolling buffer preceding an event."""
        cutoff = time.monotonic() - max(0.0, float(seconds))
        with self._recent_frames_lock:
            frames = [
                frame.copy()
                for captured_at, frame in self._recent_frames
                if captured_at >= cutoff
            ]
        return frames[-max(1, int(max_count)):]

    @staticmethod
    def is_frame_usable(frame: Optional[np.ndarray]) -> bool:
        """Reject empty and almost perfectly black camera warm-up frames."""
        if frame is None or frame.size == 0:
            return False
        return float(frame.mean()) > 2.0 or float(frame.std()) > 2.0

    def _capture_warmed_frame(self) -> Optional[np.ndarray]:
        frame = self._read_warmup_sequence(_COMMAND_WARMUP_FRAMES)
        if frame is not None:
            return frame

        log.warning("Camera returned only black/invalid frames; reopening it")
        if not self._camera.reopen():
            return None
        self._prev_gray = None
        return self._read_warmup_sequence(_COMMAND_REOPEN_WARMUP_FRAMES)

    def _read_warmup_sequence(self, count: int) -> Optional[np.ndarray]:
        latest: Optional[np.ndarray] = None
        for index in range(count):
            frame = self._camera.read_frame()
            if self.is_frame_usable(frame):
                latest = frame.copy()
            if index < count - 1:
                time.sleep(_COMMAND_WARMUP_DELAY)
        return latest

    def set_preview_callback(
        self, cb: Optional[Callable[[np.ndarray], None]]
    ) -> None:
        """Set or clear the per-frame preview callback.
        Immediately interrupts any ongoing sleep so the preview gets a frame quickly.
        The debug preview bypasses suspend state — it works outside work hours too.
        """
        self._preview_update = cb
        self._wakeup_event.set()
        self._suspend_event.set()

    def set_camera_switched_callback(self, cb: Optional[Callable]) -> None:
        self._on_camera_switched = cb

    def switch_camera(self, device_index: int) -> bool:
        """Switch to a different camera (thread-safe).

        Uses the camera_list provided at construction — does NOT call
        enumerate_cameras() again, because the detector is currently
        holding a camera and a re-enumeration would fail to open it,
        shrinking the list.
        """
        with self._switch_lock:
            if device_index == self._camera.device_index:
                return True

            log.info(f"Switching camera {self._camera.device_index} -> {device_index}")
            self._camera.close()
            self._camera.device_index = device_index
            self._prev_gray = None  # reset frame-diff baseline
            with self._recent_frames_lock:
                self._recent_frames.clear()

            if not self._camera.open():
                log.error(f"Failed to open camera index={device_index}")
                self._on_status_change("error")
                return False

            self._cfg["camera"]["device_index"] = device_index
            try:
                save_config(self._cfg)
            except Exception as exc:
                log.warning(f"Cannot save config after camera switch: {exc}")

            self._wakeup_event.set()
            self._suspend_event.set()
            log.info(f"Camera switched to index={device_index}")

            if self._on_camera_switched:
                try:
                    # Pass the stored list — no re-enumeration
                    self._on_camera_switched(self._camera_list, device_index)
                except Exception as exc:
                    log.warning(f"on_camera_switched callback error: {exc}")
            return True

    @property
    def camera_count(self) -> int:
        return len(self._camera_list)

    def cycle_camera(self) -> Optional[Tuple[int, str]]:
        """Switch to the next enumerated camera, wrapping at the end."""
        if len(self._camera_list) <= 1:
            return None

        current_index = self._camera.device_index
        current_position = next(
            (
                position
                for position, (device_index, _name) in enumerate(self._camera_list)
                if device_index == current_index
            ),
            -1,
        )
        next_camera = self._camera_list[(current_position + 1) % len(self._camera_list)]
        if not self.switch_camera(next_camera[0]):
            return None
        return next_camera

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        self._on_status_change("active")
        while not self._stop_event.is_set():
            self._maybe_cleanup_evidence()
            if self._suspended and self._preview_update is None:
                # Suspended outside work hours and no preview open -> wait
                self._suspend_event.clear()
                self._suspend_event.wait(timeout=1.0)
                continue

            frame = self._camera.read_frame()
            if frame is None:
                time.sleep(1)
                continue
            if not self.is_frame_usable(frame):
                self._invalid_monitor_frames += 1
                log.warning(
                    "Ignoring black/invalid monitoring frame (consecutive=%s)",
                    self._invalid_monitor_frames,
                )
                if self._invalid_monitor_frames >= _COMMAND_WARMUP_FRAMES:
                    log.warning("Monitoring camera stayed black; reopening it")
                    if self._camera.reopen():
                        self._prev_gray = None
                    self._invalid_monitor_frames = 0
                self._interruptible_sleep(_COMMAND_WARMUP_DELAY)
                continue
            self._invalid_monitor_frames = 0

            self._record_recent_frame(frame)

            if self._preview_update:
                try:
                    self._preview_update(frame)
                except Exception:
                    pass

            event_type, has_change = self._classify_change(frame)

            if has_change:
                self._last_motion_time = time.time()
                if self._mode == "A":
                    log.info(f"{event_type} detected -> switch to Mode B (high-sensitivity)")
                    self._mode = "B"
                self._trigger_alert(frame, event_type)

            if self._mode == "B":
                idle = time.time() - self._last_motion_time
                if idle >= _MODE_B_IDLE_TIMEOUT:
                    log.info(f"No motion for {idle:.1f}s -> switch back to Mode A")
                    self._mode = "A"

            if self._preview_update is not None or self._mode == "B":
                self._interruptible_sleep(1.0 / self._fps_high)
            else:
                self._interruptible_sleep(self._poll_interval)

    def _interruptible_sleep(self, seconds: float) -> None:
        self._wakeup_event.wait(timeout=seconds)
        self._wakeup_event.clear()

    def _record_recent_frame(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        if now - self._last_evidence_frame_at < 1.0 / self._evidence_fps:
            return
        with self._recent_frames_lock:
            self._recent_frames.append((now, frame.copy()))
        self._last_evidence_frame_at = now

    def _maybe_cleanup_evidence(self) -> None:
        now = time.monotonic()
        if now < self._next_evidence_cleanup_at:
            return
        self._cleanup_old_evidence()
        self._next_evidence_cleanup_at = now + self._evidence_cleanup_interval

    def _cleanup_old_evidence(self, now: Optional[float] = None) -> int:
        """Delete only expired automatic alert/person JPEG evidence files."""
        cutoff = (time.time() if now is None else now) - (
            self._evidence_retention_days * 86400.0
        )
        removed = 0
        log_dir = Path(self._log_dir)
        for pattern in ("alert_*.jpg", "person_*.jpg"):
            for image_path in log_dir.glob(pattern):
                try:
                    if image_path.is_file() and image_path.stat().st_mtime < cutoff:
                        image_path.unlink()
                        removed += 1
                except OSError as exc:
                    log.warning(
                        "Could not remove expired evidence %s (%s)",
                        image_path,
                        type(exc).__name__,
                    )
        if removed:
            log.info(
                "Removed %s expired evidence image(s); retention=%s days",
                removed,
                self._evidence_retention_days,
            )
        else:
            log.debug(
                "Evidence cleanup complete; no images older than %s days",
                self._evidence_retention_days,
            )
        return removed

    # ------------------------------------------------------------------
    # Change classification
    # ------------------------------------------------------------------

    def _classify_change(self, frame: np.ndarray):
        """
        Classify the current frame change as 'lighting', 'movement', or None.

        Returns (event_type, has_change):
          - ('lighting', True)  : global brightness delta exceeds threshold (lights on/off)
          - ('movement', True)  : localised contour motion detected
          - (None, False)       : no significant change
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return None, False

        prev_gray = self._prev_gray
        self._prev_gray = gray

        # Lighting change: whole-frame brightness shift
        brightness_delta = float(gray.mean()) - float(prev_gray.mean())
        if abs(brightness_delta) > self._brightness_threshold:
            if brightness_delta > 0:
                log.debug(
                    f"Dark-to-bright change detected (brightness delta={brightness_delta:.1f})"
                )
                return "lighting", True
            log.debug(
                f"Bright-to-dark change ignored (brightness delta={brightness_delta:.1f})"
            )
            return None, False

        # Movement: localised frame-diff contours
        diff = cv2.absdiff(prev_gray, gray)
        _, thresh = cv2.threshold(diff, self._pixel_threshold, 255, cv2.THRESH_BINARY)
        thresh = cv2.dilate(thresh, None, iterations=2)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            if cv2.contourArea(cnt) >= self._contour_min_area:
                log.debug(f"Movement detected (contour area={cv2.contourArea(cnt):.0f})")
                return "movement", True

        return None, False

    # ------------------------------------------------------------------
    # Alert with adaptive backoff
    # ------------------------------------------------------------------

    def _trigger_alert(self, frame: np.ndarray, event_type: str) -> None:
        now = time.time()
        state = self._alert_state.setdefault(
            event_type, {"last_time": 0.0, "backoff_index": 0}
        )
        elapsed = now - state["last_time"]

        # Quiet reset: long gap since last alert means a new activity session
        if state["backoff_index"] > 0 and elapsed > self._quiet_reset_sec:
            log.info(f"Backoff reset — {elapsed:.0f}s since last alert (quiet period)")
            state["backoff_index"] = 0

        current_debounce = self._backoff_intervals[state["backoff_index"]]
        if elapsed < current_debounce:
            remaining = current_debounce - elapsed
            log.debug(f"Alert debounced — next in {remaining:.0f}s (interval={current_debounce}s)")
            return

        state["last_time"] = now
        next_idx = min(state["backoff_index"] + 1, len(self._backoff_intervals) - 1)
        next_interval = self._backoff_intervals[next_idx]
        log.info(f"Next alert interval: {next_interval}s")
        state["backoff_index"] = next_idx

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        img_path = os.path.join(self._log_dir, f"alert_{ts}.jpg")
        cv2.imwrite(img_path, frame)
        log.warning(f"ALERT [{event_type}]: snapshot -> {img_path}")

        try:
            self._on_alert(img_path, event_type)
        except Exception as exc:
            log.exception(f"on_alert callback failed: {exc}")

    def _handle_camera_error(self) -> None:
        self._on_status_change("error")
        self._on_camera_error()
