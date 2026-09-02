"""Send a generated PNG through the configured Telegram notifier."""
from __future__ import annotations

import struct
import sys
import tempfile
import zlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from notify.telegram import TelegramNotifier
from utils.config_loader import load_config


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", checksum)
    )


def _test_png(width: int = 640, height: int = 360) -> bytes:
    row = b"\x00" + (b"\x22\x99\xee" * width)
    pixels = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + _chunk(b"IDAT", zlib.compress(pixels, 9))
        + _chunk(b"IEND", b"")
    )


def main() -> int:
    notifier = TelegramNotifier(load_config())
    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "smartcam_test.png"
        image_path.write_bytes(_test_png())
        sent = notifier.send_alert(
            str(image_path),
            "SmartCam Watcher - Telegram test alert",
        )

    print(f"test_sent: {sent}")
    print(f"chat_id_saved: {notifier.is_configured}")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
