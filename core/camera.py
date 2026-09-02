"""
Camera enumeration and frame capture module.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("smartcam.camera")


# ---------------------------------------------------------------------------
# Windows PnP camera name query
# ---------------------------------------------------------------------------

def _get_pnp_camera_names() -> List[str]:
    """
    Query Windows PnP for camera friendly names via PowerShell.
    Filters out IR cameras (even-numbered MI interface or 'IR' in name).
    Sorted by InstanceId to match the order Windows enumerates them.

    Returns an empty list on failure; callers fall back to 'Camera N' labels.
    """
    ps_script = r"""
$cams = Get-PnpDevice -Class Camera -Status OK -ErrorAction SilentlyContinue |
    Where-Object {
        $_.FriendlyName -notmatch '\bIR\b' -and
        $_.InstanceId   -notmatch '&MI_0[2468A-Fa-f]\\'
    } |
    Sort-Object InstanceId |
    Select-Object -ExpandProperty FriendlyName

if ($cams) { $cams | ConvertTo-Json -Compress } else { '[]' }
"""
    try:
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            log.debug("PnP camera name query timed out")
            return []
        raw = stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, str):
            return [data]
        return list(data)
    except BaseException as exc:          # catches KeyboardInterrupt too
        log.debug(f"PnP camera name query failed: {exc}")
        return []


def enumerate_cameras(max_index: int = 8) -> List[Tuple[int, str]]:
    """
    Probe indices 0..max_index-1 and return working cameras as
    [(device_index, friendly_name), ...].

    Friendly names come from the PnP query; falls back to 'Camera N'
    if the query fails or returns fewer names than cameras found.
    """
    pnp_names = _get_pnp_camera_names()
    log.debug(f"PnP camera names: {pnp_names}")

    available: List[Tuple[int, str]] = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                n = len(available)
                friendly = pnp_names[n] if n < len(pnp_names) else f"Camera {i}"
                available.append((i, friendly))
                log.debug(f"Found camera: index={i}, name={friendly!r}")
        else:
            cap.release()
    return available


class CameraCapture:
    """
    Wraps an OpenCV VideoCapture with open / read_frame / close interface.

    Parameters
    ----------
    device_index : int
        Camera index (from config.yaml camera.device_index).
    on_error : Callable, optional
        Called after _MAX_FAILURES consecutive read failures.
    """

    def __init__(
        self,
        device_index: int = 0,
        on_error: Optional[Callable[[], None]] = None,
    ) -> None:
        self.device_index = device_index
        self.on_error = on_error
        self._cap: Optional[cv2.VideoCapture] = None
        self._consecutive_failures = 0
        self._MAX_FAILURES = 5
        self._lock = threading.RLock()

    def open(self) -> bool:
        with self._lock:
            log.info(f"Opening camera index={self.device_index}")
            self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
            if not self._cap.isOpened():
                log.error(f"Cannot open camera index={self.device_index}")
                self._notify_error()
                return False
            self._consecutive_failures = 0
            log.info("Camera opened successfully")
            return True

    def read_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                log.warning("Camera not open, cannot read frame")
                return None
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._consecutive_failures += 1
                log.warning(f"Frame read failed (consecutive={self._consecutive_failures})")
                if self._consecutive_failures >= self._MAX_FAILURES:
                    self._notify_error()
                return None
            self._consecutive_failures = 0
            return frame

    def close(self) -> None:
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
                log.info("Camera closed")

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._cap is not None and self._cap.isOpened()

    def _notify_error(self) -> None:
        log.error("Camera error threshold reached, triggering on_error callback")
        if self.on_error:
            try:
                self.on_error()
            except Exception as exc:
                log.exception(f"on_error callback raised: {exc}")
