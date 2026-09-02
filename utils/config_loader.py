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
        "poll_interval_sec": 30,
    },
    "detection": {
        "pixel_threshold": 25,
        "contour_min_area": 800,
        "brightness_change_threshold": 25,
        "backoff_intervals_sec": [300, 900, 1800, 3600],
        "quiet_reset_sec": 1800,
    },
    "telegram": {
        "credentials_file": "telegram_credentials.yaml",
        "timeout_sec": 20,
        "retries": 3,
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
