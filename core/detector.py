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
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from core.camera import CameraCapture
from utils.config_loader import save_config

log = logging.getLogger("smartcam.detector")

_MODE_B_IDLE_TIMEOUT = 10.0   # seconds of no motion before reverting to Mode A


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
        self._backoff_index: int = 0

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
        """Read one frame for an on-demand Telegram command."""
        with self._command_capture_lock:
            frame = self._camera.read_frame()
            return frame.copy() if frame is not None else None

    def capture_frames(self, frame_count: int, fps: float) -> List[np.ndarray]:
        """Capture a short, low-frame-rate sequence without changing monitor mode."""
        count = max(1, int(frame_count))
        interval = 1.0 / max(0.2, float(fps))
        frames: List[np.ndarray] = []

        with self._command_capture_lock:
            next_frame_at = time.monotonic()
            for index in range(count):
                frame = self._camera.read_frame()
                if frame is not None:
                    frames.append(frame.copy())
                if index == count - 1:
                    break
                next_frame_at += interval
                remaining = next_frame_at - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)

        return frames

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

    def switch_camera(self, device_index: int) -> None:
        """Switch to a different camera (thread-safe).

        Uses the camera_list provided at construction — does NOT call
        enumerate_cameras() again, because the detector is currently
        holding a camera and a re-enumeration would fail to open it,
        shrinking the list.
        """
        with self._switch_lock:
            if device_index == self._camera.device_index:
                return

            log.info(f"Switching camera {self._camera.device_index} -> {device_index}")
            self._camera.close()
            self._camera.device_index = device_index
            self._prev_gray = None  # reset frame-diff baseline

            if not self._camera.open():
                log.error(f"Failed to open camera index={device_index}")
                self._on_status_change("error")
                return

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

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        self._on_status_change("active")
        while not self._stop_event.is_set():
            if self._suspended and self._preview_update is None:
                # Suspended outside work hours and no preview open -> wait
                self._suspend_event.clear()
                self._suspend_event.wait(timeout=1.0)
                continue

            frame = self._camera.read_frame()
            if frame is None:
                time.sleep(1)
                continue

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
        brightness_delta = abs(float(gray.mean()) - float(prev_gray.mean()))
        if brightness_delta > self._brightness_threshold:
            log.debug(f"Lighting change detected (brightness delta={brightness_delta:.1f})")
            return "lighting", True

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
        elapsed = now - self._last_alert_time

        # Quiet reset: long gap since last alert means a new activity session
        if self._backoff_index > 0 and elapsed > self._quiet_reset_sec:
            log.info(f"Backoff reset — {elapsed:.0f}s since last alert (quiet period)")
            self._backoff_index = 0

        current_debounce = self._backoff_intervals[self._backoff_index]
        if elapsed < current_debounce:
            remaining = current_debounce - elapsed
            log.debug(f"Alert debounced — next in {remaining:.0f}s (interval={current_debounce}s)")
            return

        self._last_alert_time = now
        next_idx = min(self._backoff_index + 1, len(self._backoff_intervals) - 1)
        next_interval = self._backoff_intervals[next_idx]
        log.info(f"Next alert interval: {next_interval}s")
        self._backoff_index = next_idx

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
