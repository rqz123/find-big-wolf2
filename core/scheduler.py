"""
Work-hours scheduler.

Checks every check_interval_sec seconds whether the current time falls
inside the configured work window, and calls detector.suspend() or
detector.resume() accordingly.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from core.detector import MotionDetector

log = logging.getLogger("smartcam.scheduler")


def is_work_time(cfg: Dict[str, Any]) -> bool:
    """Return True if the current time is inside the configured work window."""
    sched = cfg.get("schedule", {})
    weekdays = sched.get("weekdays", [0, 1, 2, 3, 4])  # 0=Mon
    start_str = sched.get("start", "09:00")
    end_str = sched.get("end", "18:00")

    now = datetime.now()
    if now.weekday() not in weekdays:
        return False

    start_h, start_m = (int(x) for x in str(start_str).split(":"))
    end_h, end_m = (int(x) for x in str(end_str).split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now.hour * 60 + now.minute

    return start_minutes <= now_minutes < end_minutes


class Scheduler:
    """
    Polls work-time window and drives MotionDetector suspend/resume.

    Parameters
    ----------
    cfg : dict
        Full config dict.
    detector : MotionDetector
    check_interval_sec : float
        How often to re-check (default 30 s for fast boundary response).
    """

    def __init__(
        self,
        cfg: Dict[str, Any],
        detector: "MotionDetector",
        check_interval_sec: float = 30.0,
    ) -> None:
        self._cfg = cfg
        self._detector = detector
        self._check_interval = check_interval_sec
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_state: bool | None = None

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("Scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Scheduler stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            in_work = is_work_time(self._cfg)
            if in_work != self._last_state:
                self._last_state = in_work
                if in_work:
                    log.info("Entering work-hours window -> resuming detector")
                    self._detector.resume()
                else:
                    log.info("Leaving work-hours window -> suspending detector")
                    self._detector.suspend()
            self._stop_event.wait(timeout=self._check_interval)
