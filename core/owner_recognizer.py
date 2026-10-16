"""Recognize the owner's face locally with OpenCV YuNet + SFace.

Reference photos live in ``owner_faces/`` (Git-ignored).  Each photo is
reduced to one SFace embedding at startup; a camera face matches the owner
when its cosine similarity to any reference reaches the configured threshold.
SFace model: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np

log = logging.getLogger("smartcam.owner")

_ROOT = Path(__file__).resolve().parents[1]
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
_DETECT_MAX_WIDTH = 1280


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else _ROOT / path


class OwnerRecognizer:
    """Decide whether a frame shows the enrolled owner; frames never leave the PC."""

    def __init__(self, cfg: dict) -> None:
        det_cfg = cfg.get("detection", {})
        self._enabled = bool(det_cfg.get("owner_recognition_enabled", True))
        self._faces_dir = _resolve(det_cfg.get("owner_faces_dir", "owner_faces"))
        self._threshold = max(
            0.2, min(0.95, float(det_cfg.get("owner_match_threshold", 0.45)))
        )
        self._lock = threading.Lock()
        self._gallery: List[np.ndarray] = []
        self._detector = None
        self._recognizer = None
        if not self._enabled:
            log.info("Owner recognition disabled in config")
            return

        face_model = _resolve(
            det_cfg.get("face_model_path", "models/face_detection_yunet_2023mar.onnx")
        )
        sface_model = _resolve(
            det_cfg.get(
                "owner_model_path", "models/face_recognition_sface_2021dec.onnx"
            )
        )
        try:
            for path in (face_model, sface_model):
                if not path.is_file():
                    raise FileNotFoundError(str(path))
            self._detector = cv2.FaceDetectorYN.create(
                str(face_model), "", (320, 320), 0.8, 0.3, 5000
            )
            self._recognizer = cv2.FaceRecognizerSF.create(str(sface_model), "")
        except (AttributeError, OSError, cv2.error) as exc:
            log.warning(
                "Owner recognition unavailable (%s); every person will alert",
                type(exc).__name__,
            )
            self._detector = None
            self._recognizer = None
            return

        self._load_gallery()

    @property
    def available(self) -> bool:
        return self._recognizer is not None and bool(self._gallery)

    @property
    def reference_count(self) -> int:
        return len(self._gallery)

    def match_score(self, frame: Optional[np.ndarray]) -> float:
        """Best owner similarity of any face in the frame (0 when none)."""
        if not self.available or frame is None or not frame.size:
            return 0.0
        best = 0.0
        with self._lock:
            try:
                for face in self._detect_faces(frame):
                    feature = self._feature(frame, face)
                    for reference in self._gallery:
                        score = self._recognizer.match(
                            feature, reference, cv2.FaceRecognizerSF_FR_COSINE
                        )
                        best = max(best, float(score))
            except cv2.error as exc:
                log.warning("Owner face matching failed (%s)", type(exc).__name__)
                return 0.0
        return best

    def is_owner(self, frame: Optional[np.ndarray]) -> bool:
        score = self.match_score(frame)
        if score > 0:
            log.debug("Owner face similarity %.3f (threshold %.2f)", score, self._threshold)
        return score >= self._threshold

    def enroll(self, frame: Optional[np.ndarray]) -> Optional[str]:
        """Save the frame as a reference photo; return an error message or None."""
        if self._recognizer is None:
            return "人脸识别模型不可用。"
        if frame is None or not frame.size:
            return "摄像头当前不可用。"
        with self._lock:
            faces = self._detect_faces(frame)
            if len(faces) != 1:
                return f"需要画面中恰好一张清晰正脸，当前检测到 {len(faces)} 张。"
            feature = self._feature(frame, faces[0])
            self._faces_dir.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime("owner_%Y%m%d_%H%M%S.jpg")
            if not cv2.imwrite(str(self._faces_dir / name), frame):
                return "无法保存参考照片。"
            self._gallery.append(feature)
        log.info("Owner reference enrolled: %s (total %s)", name, len(self._gallery))
        return None

    def _load_gallery(self) -> None:
        if not self._faces_dir.is_dir():
            log.info("No owner reference folder (%s); owner recognition idle", self._faces_dir)
            return
        for path in sorted(self._faces_dir.iterdir()):
            if path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            image = cv2.imread(str(path))
            if image is None:
                log.warning("Cannot read owner reference %s", path.name)
                continue
            faces = self._detect_faces(image)
            if not faces:
                log.warning("No face found in owner reference %s", path.name)
                continue
            largest = max(faces, key=lambda face: face[2] * face[3])
            self._gallery.append(self._feature(image, largest))
        log.info(
            "Owner recognition loaded %s reference face(s) from %s",
            len(self._gallery),
            self._faces_dir,
        )

    def _detect_faces(self, frame: np.ndarray) -> List[np.ndarray]:
        """Detect on a downscaled copy, returning faces in full-frame coordinates."""
        height, width = frame.shape[:2]
        scale = min(1.0, _DETECT_MAX_WIDTH / width)
        small = frame
        if scale < 1.0:
            small = cv2.resize(
                frame,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        self._detector.setInputSize((small.shape[1], small.shape[0]))
        _retval, faces = self._detector.detect(small)
        result = []
        for face in faces if faces is not None else []:
            face = face.copy()
            face[:14] /= scale
            result.append(face)
        return result

    def _feature(self, frame: np.ndarray, face: np.ndarray) -> np.ndarray:
        aligned = self._recognizer.alignCrop(frame, face)
        return self._recognizer.feature(aligned).copy()
