"""
Config load / save: reads and writes config.yaml.
"""
import os
from typing import Any, Dict

import yaml

DEFAULT_CONFIG: Dict[str, Any] = {
    "schedule": {
        "start": "09:00",
        "end": "18:00",
        "weekdays": [0, 1, 2, 3, 4],  # 0=Mon ... 4=Fri
    },
    "camera": {
        "device_index": -1,   # -1 = first-run, prompt user to select
        "fps_high": 15,
        "poll_interval_sec": 0.5,
    },
    "detection": {
        "pixel_threshold": 25,
        "contour_min_area": 800,
        "brightness_change_threshold": 25,
        "evidence_fps": 2,
        "pre_event_buffer_sec": 3,
        "person_tracking_duration_sec": 15,
        "evidence_retention_days": 7,
        "evidence_cleanup_interval_hours": 6,
        "person_model_path": "models/person_detection_nanodet_2022nov.onnx",
        "person_confidence_threshold": 0.30,
        "person_direct_confidence_threshold": 0.50,
        "face_model_path": "models/face_detection_yunet_2023mar.onnx",
        "face_confidence_threshold": 0.60,
        "person_detection_regions": [
            [0.0, 0.0, 1.0, 1.0],
            [0.25, 0.10, 0.80, 1.0],
            [0.45, 0.20, 0.85, 0.95],
        ],
        "person_nms_threshold": 0.3,
        "backoff_intervals_sec": [300, 900, 1800, 3600],
        "quiet_reset_sec": 1800,
    },
    "telegram": {
        "credentials_file": "telegram_credentials.yaml",
        "timeout_sec": 20,
        "retries": 3,
        "command_poll_timeout_sec": 20,
        "clip_duration_sec": 5,
        "clip_fps": 2,
        "clip_max_width": 640,
    },
    "logging": {
        "level": "DEBUG",
    },
}

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml")


def load_config(path: str = _CONFIG_PATH) -> Dict[str, Any]:
    """Load config.yaml; write defaults and return them if the file does not exist."""
    if not os.path.exists(path):
        save_config(DEFAULT_CONFIG, path)
        return dict(DEFAULT_CONFIG)

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    for key, val in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = val

    return cfg


def save_config(cfg: Dict[str, Any], path: str = _CONFIG_PATH) -> None:
    """Write the config dict back to config.yaml."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False)
