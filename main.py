"""
SmartCam Watcher - entry point.

Startup sequence:
  1. Load config.yaml
  2. Init logger
  3. First run (device_index == -1): enumerate cameras, prompt user to select
  4. Init: Notifier / PreviewWindow / Detector / Scheduler / TrayApp
  5. Start Detector and Scheduler in background threads
  6. Block main thread in TrayApp.run() (pystray requires the main thread)
"""
from __future__ import annotations

import os
import sys
import threading

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from utils.config_loader import load_config, save_config
from utils.logger import setup_logger, get_logger
from core.camera import enumerate_cameras
from core.detector import MotionDetector
from core.scheduler import Scheduler
from core.telegram_controller import TelegramCameraController
from notify.telegram import TelegramNotifier
from notify.telegram_commands import TelegramCommandListener
from ui.preview import PreviewWindow
from ui.tray import TrayApp

_CONFIG_PATH = os.path.join(_ROOT, "config.yaml")
_LOG_DIR = os.path.join(_ROOT, "logs")


# ── First-run camera selection ────────────────────────────────────────

def _select_camera(cfg: dict) -> dict:
    log = get_logger("smartcam.main")
    log.info("First run: enumerating cameras ...")
    print("\n=== SmartCam Watcher - First-time Setup ===")
    print("Scanning cameras, please wait...\n")

    cameras = enumerate_cameras(max_index=8)

    if not cameras:
        print("ERROR: No cameras found. Check device connections and restart.")
        log.error("No cameras found at first-run setup, exiting.")
        sys.exit(1)

    print("Available cameras:")
    for i, (dev_idx, name) in enumerate(cameras):
        print(f"  [{i}] {name}  (device index={dev_idx})")

    while True:
        try:
            choice = int(input(f"\nSelect camera number [0-{len(cameras)-1}]: ").strip())
            if 0 <= choice < len(cameras):
                break
        except (ValueError, KeyboardInterrupt):
            pass
        print("Invalid input, please retry.")

    selected_dev_idx = cameras[choice][0]
    cfg["camera"]["device_index"] = selected_dev_idx
    save_config(cfg, _CONFIG_PATH)
    print(f"\nSelected: {cameras[choice][1]} — config saved.\n")
    log.info(f"Camera selected: device_index={selected_dev_idx}")
    return cfg


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    cfg = load_config(_CONFIG_PATH)

    log_level = cfg.get("logging", {}).get("level", "DEBUG")
    setup_logger(level_str=log_level, log_dir=_LOG_DIR)
    log = get_logger("smartcam.main")
    log.info("SmartCam Watcher starting ...")

    if cfg["camera"].get("device_index", -1) == -1:
        cfg = _select_camera(cfg)

    current_dev_idx: int = cfg["camera"]["device_index"]

    camera_list = enumerate_cameras(max_index=8)
    if not camera_list:
        log.error("No cameras found at startup, exiting.")
        sys.exit(1)

    # ── Init modules ─────────────────────────────────────────────────
    notifier = TelegramNotifier(cfg)
    preview = PreviewWindow()

    tray = TrayApp(
        preview=preview,
        log_dir=_LOG_DIR,
        camera_list=camera_list,
        current_device_index=current_dev_idx,
    )

    def on_alert(image_path: str, event_type: str = "movement") -> None:
        log.warning(f"Alert triggered [{event_type}]: {image_path}")
        threading.Thread(
            target=controller.send_motion_alert,
            args=(image_path, event_type),
            daemon=True,
        ).start()

    def on_camera_error() -> None:
        tray.set_status("error")

    def on_status_change(status: str) -> None:
        tray.set_status(status)

    def on_camera_switched(updated_camera_list, new_device_index: int) -> None:
        tray.update_camera_selection(updated_camera_list, new_device_index)
        if preview.is_running:
            preview.notify_camera_changed(updated_camera_list, new_device_index)

    detector = MotionDetector(
        cfg=cfg,
        camera_list=camera_list,
        on_alert=on_alert,
        on_camera_error=on_camera_error,
        on_status_change=on_status_change,
    )
    detector.set_camera_switched_callback(on_camera_switched)

    scheduler = Scheduler(cfg=cfg, detector=detector)
    controller = TelegramCameraController(
        cfg=cfg,
        detector=detector,
        notifier=notifier,
        on_pause_change=tray.set_monitoring_paused,
    )
    command_listener = TelegramCommandListener(
        notifier=notifier,
        on_command=controller.handle,
        poll_timeout_sec=cfg.get("telegram", {}).get("command_poll_timeout_sec", 20),
    )

    tray.set_detector(detector)
    tray.set_notifier(notifier)

    # ── Start background threads ──────────────────────────────────────
    detector.start()
    scheduler.start()
    command_listener.start()
    log.info("Detector, Scheduler, and Telegram commands started")

    # ── Main thread: tray (blocks until user quits) ───────────────────
    try:
        tray.run()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received")
    finally:
        log.info("Shutting down ...")
        command_listener.stop()
        preview.stop()
        scheduler.stop()
        detector.stop()
        log.info("SmartCam Watcher exited cleanly")


if __name__ == "__main__":
    main()
