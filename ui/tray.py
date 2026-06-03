"""
System tray UI.

Menu layout:
  Status: Running / Suspended / Camera Error   (non-clickable)
  ──────────────────────────────────────────
  [Debug] Open video preview  (toggle)
  Select Camera >
    * DTEN ME Pro Camera Array
      Integrated Webcam
  ──────────────────────────────────────────
  WhatsApp Setup (scan QR to link device)
  ──────────────────────────────────────────
  Open Logs
  Quit

Tray icon colors:
  Green  - active (detecting)
  Blue   - suspended (outside work hours)
  Red    - error (camera failure)
"""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING, List, Optional, Tuple

from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as Item, Menu

if TYPE_CHECKING:
    from ui.preview import PreviewWindow
    from core.detector import MotionDetector
    from notify.whatsapp import WhatsAppNotifier

log = logging.getLogger("smartcam.tray")

_COLORS = {
    "active":    (46, 204, 113),   # green
    "suspended": (52, 152, 219),   # blue
    "error":     (231, 76,  60),   # red
}
_STATUS_LABELS = {
    "active":    "Status: Running",
    "suspended": "Status: Suspended (outside work hours)",
    "error":     "Status: Camera Error",
}
_ICON_SIZE = (64, 64)


def _make_icon(color: tuple) -> Image.Image:
    img = Image.new("RGBA", _ICON_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = 6
    draw.ellipse([m, m, _ICON_SIZE[0] - m, _ICON_SIZE[1] - m], fill=color + (255,))
    return img


class TrayApp:
    """System tray icon and menu. Must call run() from the main thread."""

    def __init__(
        self,
        preview: "PreviewWindow",
        log_dir: str,
        camera_list: List[Tuple[int, str]],
        current_device_index: int,
    ) -> None:
        self._preview = preview
        self._log_dir = log_dir
        self._status: str = "active"
        self._icon: Optional[pystray.Icon] = None
        self._detector: Optional["MotionDetector"] = None
        self._notifier: Optional["WhatsAppNotifier"] = None
        self._preview_open: bool = False
        self._camera_list: List[Tuple[int, str]] = camera_list
        self._current_device_index: int = current_device_index

    def set_detector(self, detector: "MotionDetector") -> None:
        self._detector = detector

    def set_notifier(self, notifier: "WhatsAppNotifier") -> None:
        self._notifier = notifier

    def update_camera_selection(
        self,
        camera_list: List[Tuple[int, str]],
        current_device_index: int,
    ) -> None:
        """Refresh menu checkmark after a camera switch (any thread)."""
        self._camera_list = camera_list
        self._current_device_index = current_device_index
        self._rebuild_menu()

    # ------------------------------------------------------------------
    # Run / stop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._icon = pystray.Icon(
            name="SmartCam Watcher",
            icon=_make_icon(_COLORS["active"]),
            title="SmartCam Watcher",
            menu=self._build_menu(),
        )
        log.info("Tray icon running")
        self._icon.run()

    def stop(self) -> None:
        if self._icon:
            self._icon.stop()

    # ------------------------------------------------------------------
    # Status update (any thread)
    # ------------------------------------------------------------------

    def set_status(self, status: str) -> None:
        if status not in _COLORS:
            return
        self._status = status
        if self._icon:
            self._icon.icon = _make_icon(_COLORS[status])
            self._icon.title = f"SmartCam — {_STATUS_LABELS[status]}"
            self._rebuild_menu()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> Menu:
        preview_label = "Close Video Preview" if self._preview_open else "Open Video Preview"

        cam_items = []
        for dev_idx, name in self._camera_list:
            is_current = dev_idx == self._current_device_index
            label = f"* {name}" if is_current else f"  {name}"
            def _make_cb(idx):
                def _cb(icon, item):
                    self._select_camera(idx)
                return _cb
            cam_items.append(Item(label, _make_cb(dev_idx)))

        camera_submenu = Item(
            "Select Camera",
            Menu(*cam_items) if cam_items
            else Menu(Item("(no cameras available)", None, enabled=False)),
        )

        return Menu(
            Item(_STATUS_LABELS.get(self._status, "Status: Unknown"), None, enabled=False),
            Menu.SEPARATOR,
            Item("[Debug] " + preview_label, self._toggle_preview),
            camera_submenu,
            Menu.SEPARATOR,
            Item("WhatsApp Setup (scan QR to link device)", self._whatsapp_setup),
            Item("Send Test Alert", self._send_test_alert),
            Menu.SEPARATOR,
            Item("Open Logs", self._open_logs),
            Item("Quit", self._quit),
        )

    def _rebuild_menu(self) -> None:
        if self._icon:
            self._icon.menu = self._build_menu()
            try:
                self._icon.update_menu()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _toggle_preview(self, icon, item) -> None:
        if self._preview_open:
            self._preview.stop()
            self._preview_open = False
            if self._detector:
                self._detector.set_preview_callback(None)
            log.info("Preview closed via menu")
        else:
            self._preview_open = True
            self._preview.set_camera_info(self._camera_list, self._current_device_index)
            self._preview.start(
                on_closed=self._on_preview_closed,
                on_switch_camera=self._on_preview_switch_camera,
            )
            if self._detector:
                self._detector.set_preview_callback(self._preview.update_frame)
            log.info("Preview opened via menu")
        self._rebuild_menu()

    def _on_preview_closed(self) -> None:
        self._preview_open = False
        if self._detector:
            self._detector.set_preview_callback(None)
        self._rebuild_menu()

    def _on_preview_switch_camera(self, new_device_index: int) -> None:
        self._select_camera(new_device_index)

    def _select_camera(self, device_index: int) -> None:
        if self._detector:
            self._detector.switch_camera(device_index)
        self._current_device_index = device_index
        if self._preview_open:
            self._preview.notify_camera_changed(self._camera_list, device_index)
        self._rebuild_menu()

    def _whatsapp_setup(self, icon, item) -> None:
        def _run():
            if self._notifier:
                self._notifier.setup()
        threading.Thread(target=_run, name="wa-setup", daemon=True).start()

    def _send_test_alert(self, icon, item) -> None:
        """Send a test WhatsApp message with a generated placeholder image."""
        import os, tempfile
        import numpy as np
        import cv2

        def _run():
            log.info("Sending test alert ...")
            # Generate a simple test image
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(img, "SmartCam Watcher", (80, 200),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 0), 3)
            cv2.putText(img, "Test Alert", (190, 280),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.close()
            cv2.imwrite(tmp.name, img)
            try:
                if self._notifier:
                    self._notifier.send_alert(tmp.name, "SmartCam Watcher — test alert")
                    log.info("Test alert sent")
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

        threading.Thread(target=_run, name="wa-test", daemon=True).start()

    def _open_logs(self, icon, item) -> None:
        try:
            os.startfile(os.path.abspath(self._log_dir))
        except Exception as exc:
            log.warning(f"Cannot open log dir: {exc}")

    def _quit(self, icon, item) -> None:
        log.info("Quit via tray menu")
        self._preview.stop()
        icon.stop()
