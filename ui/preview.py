"""
Debug preview window.

Keyboard shortcuts (window must be in focus):
  Q       - Close preview
  C / N   - Switch to next camera
  P       - Switch to previous camera

Camera switching is debounced: a new switch is ignored if one is already
in progress or less than _SWITCH_COOLDOWN seconds have passed since the last one.
This prevents key-repeat from queuing multiple rapid switches that race
each other and drop cameras from the list.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("smartcam.preview")

_WINDOW_NAME = "SmartCam Debug Preview  [Q=Close  C/N=Next camera  P=Prev camera]"
_SWITCH_COOLDOWN = 2.0   # minimum seconds between camera switches


class PreviewWindow:
    """Thread-safe OpenCV preview window with camera-switching support."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()

        self._on_closed: Optional[Callable] = None
        self._on_switch_camera: Optional[Callable[[int], None]] = None

        self._camera_list: List[Tuple[int, str]] = []
        self._current_cam_idx: int = 0

        # Debounce state
        self._last_switch_time: float = 0.0
        self._switch_in_progress: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_camera_info(
        self,
        camera_list: List[Tuple[int, str]],
        current_device_index: int,
    ) -> None:
        """Called by main/tray to inject the camera list and current selection."""
        self._camera_list = camera_list
        for i, (dev_idx, _) in enumerate(camera_list):
            if dev_idx == current_device_index:
                self._current_cam_idx = i
                break

    def start(
        self,
        on_closed: Optional[Callable] = None,
        on_switch_camera: Optional[Callable[[int], None]] = None,
    ) -> None:
        """
        Start the preview thread.

        on_closed        : called when the window is closed by the user.
        on_switch_camera : called with the new device_index when C/N/P is pressed.
        """
        if self.is_running:
            log.warning("Preview already running")
            return
        self._on_closed = on_closed
        self._on_switch_camera = on_switch_camera
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="preview", daemon=True)
        self._thread.start()
        log.info("Preview window started")

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._thread = None
        log.info("Preview window stopped")

    def update_frame(self, frame: np.ndarray) -> None:
        """Called by MotionDetector on every frame (thread-safe)."""
        if not self.is_running:
            return
        with self._lock:
            self._latest_frame = frame.copy()

    def notify_camera_changed(
        self,
        camera_list: List[Tuple[int, str]],
        new_device_index: int,
    ) -> None:
        """Called after a camera switch completes to refresh overlay info."""
        self.set_camera_info(camera_list, new_device_index)
        with self._lock:
            self._latest_frame = None   # discard stale frame from old camera
        self._switch_in_progress = False

    # ------------------------------------------------------------------
    # Display loop (runs in background thread)
    # ------------------------------------------------------------------

    def _run(self) -> None:
        log.debug("Preview thread starting display loop")
        cv2.namedWindow(_WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(_WINDOW_NAME, 800, 600)

        closed_by_user = False

        while not self._stop_event.is_set():
            with self._lock:
                frame = self._latest_frame

            if frame is not None:
                display = frame.copy()
                self._draw_overlay(display)
                cv2.imshow(_WINDOW_NAME, display)
            else:
                placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(
                    placeholder, "Waiting for camera...",
                    (140, 240), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (180, 180, 180), 2,
                )
                cv2.imshow(_WINDOW_NAME, placeholder)

            key = cv2.waitKey(33) & 0xFF

            if key == ord("q"):
                log.info("Preview closed by user (Q key)")
                closed_by_user = True
                break
            elif key in (ord("c"), ord("n")):
                self._handle_switch(+1)
            elif key == ord("p"):
                self._handle_switch(-1)

            try:
                if cv2.getWindowProperty(_WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    log.info("Preview window closed by user (x button)")
                    closed_by_user = True
                    break
            except cv2.error:
                break

        cv2.destroyAllWindows()
        self._thread = None

        if closed_by_user and self._on_closed:
            try:
                self._on_closed()
            except Exception as exc:
                log.exception(f"on_closed callback error: {exc}")

    # ------------------------------------------------------------------
    # Camera switching (debounced)
    # ------------------------------------------------------------------

    def _handle_switch(self, delta: int) -> None:
        """Handle C/N/P key: cycle camera with debounce + in-progress guard."""
        if not self._camera_list:
            return

        now = time.time()
        if self._switch_in_progress:
            log.debug("Camera switch ignored: previous switch still in progress")
            return
        if now - self._last_switch_time < _SWITCH_COOLDOWN:
            log.debug("Camera switch ignored: cooldown active")
            return

        self._last_switch_time = now
        self._switch_in_progress = True

        n = len(self._camera_list)
        new_pos = (self._current_cam_idx + delta) % n
        self._current_cam_idx = new_pos
        new_dev_idx = self._camera_list[new_pos][0]
        new_name = self._camera_list[new_pos][1]

        log.info(f"Preview: switching camera -> {new_name} (index={new_dev_idx})")

        if self._on_switch_camera:
            threading.Thread(
                target=self._on_switch_camera,
                args=(new_dev_idx,),
                daemon=True,
            ).start()

    # ------------------------------------------------------------------
    # Overlay
    # ------------------------------------------------------------------

    def _draw_overlay(self, frame: np.ndarray) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")

        if self._camera_list and self._current_cam_idx < len(self._camera_list):
            cam_name = self._camera_list[self._current_cam_idx][1]
        else:
            cam_name = "Camera ?"

        # Semi-transparent top bar
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 40), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

        # Timestamp (left)
        cv2.putText(
            frame, f"[DEBUG] {ts}",
            (8, 26), cv2.FONT_HERSHEY_SIMPLEX,
            0.65, (0, 255, 0), 2, cv2.LINE_AA,
        )
        # Camera name (right)
        label = f"[C] {cam_name}"
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.putText(
            frame, label,
            (frame.shape[1] - tw - 8, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55, (0, 220, 255), 1, cv2.LINE_AA,
        )
