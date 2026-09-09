"""Local person detection backed by OpenCV DNN and NanoDet.

The NanoDet preprocessing and output decoding follow the Apache-2.0 licensed
OpenCV Model Zoo reference implementation:
https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

log = logging.getLogger("smartcam.person")

_DEFAULT_MODEL = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "person_detection_nanodet_2022nov.onnx"
)
_DEFAULT_FACE_MODEL = (
    Path(__file__).resolve().parents[1]
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)


@dataclass
class PersonScan:
    """Result of scanning a short camera sequence for people."""

    available: bool
    found: bool
    frames: List[np.ndarray]
    max_confidence: float = 0.0
    best_frame: Optional[np.ndarray] = None


class PersonDetector:
    """Detect COCO class 0 (person) locally; camera frames never leave the PC."""

    _INPUT_SIZE = (416, 416)
    _STRIDES = (8, 16, 32)
    _REG_MAX = 7

    def __init__(self, cfg: dict) -> None:
        det_cfg = cfg.get("detection", {})
        configured_path = det_cfg.get("person_model_path", str(_DEFAULT_MODEL))
        model_path = Path(configured_path)
        if not model_path.is_absolute():
            model_path = Path(__file__).resolve().parents[1] / model_path

        self._confidence = max(
            0.05, min(0.95, float(det_cfg.get("person_confidence_threshold", 0.35)))
        )
        self._direct_confidence = max(
            self._confidence,
            min(0.99, float(det_cfg.get("person_direct_confidence_threshold", 0.50))),
        )
        self._face_confidence = max(
            0.1, min(0.99, float(det_cfg.get("face_confidence_threshold", 0.6)))
        )
        self._nms_threshold = max(
            0.1, min(0.9, float(det_cfg.get("person_nms_threshold", 0.3)))
        )
        configured_regions = det_cfg.get(
            "person_detection_regions",
            [
                [0.0, 0.0, 1.0, 1.0],
                [0.25, 0.10, 0.80, 1.0],
                [0.45, 0.20, 0.85, 0.95],
            ],
        )
        self._regions = self._validate_regions(configured_regions)
        self._project = np.arange(self._REG_MAX + 1, dtype=np.float32)
        self._mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
        self._std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)
        self._anchors = self._make_anchors()
        self._net = None
        self._face_detector = None

        try:
            if not model_path.is_file():
                raise FileNotFoundError(str(model_path))
            self._net = cv2.dnn.readNet(str(model_path))
            log.info("Local person detector loaded: %s", model_path)
        except (OSError, cv2.error) as exc:
            log.error(
                "Person detector unavailable (%s); movement alerts will fail open with GIF",
                type(exc).__name__,
            )

        face_model_path = Path(
            det_cfg.get("face_model_path", str(_DEFAULT_FACE_MODEL))
        )
        if not face_model_path.is_absolute():
            face_model_path = Path(__file__).resolve().parents[1] / face_model_path
        try:
            if not face_model_path.is_file():
                raise FileNotFoundError(str(face_model_path))
            self._face_detector = cv2.FaceDetectorYN.create(
                str(face_model_path),
                "",
                (320, 320),
                self._face_confidence,
                0.3,
                5000,
            )
            log.info("Low-confidence person face confirmation loaded: %s", face_model_path)
        except (AttributeError, OSError, cv2.error) as exc:
            log.warning(
                "Face confirmation unavailable (%s); only high-confidence people will pass",
                type(exc).__name__,
            )

    @property
    def available(self) -> bool:
        return self._net is not None

    def scan(self, frames: Sequence[np.ndarray]) -> PersonScan:
        """Scan and annotate every usable frame in an evidence sequence."""
        usable = [frame.copy() for frame in frames if frame is not None and frame.size]
        if not self.available:
            return PersonScan(False, False, usable)

        annotated: List[np.ndarray] = []
        found = False
        max_confidence = 0.0
        best_frame = None
        try:
            for frame_index, frame in enumerate(usable):
                candidates = self._infer_regions(frame)
                detections = self._confirm_detections(frame, candidates)
                output = frame.copy()
                for (x1, y1, x2, y2), confidence in detections:
                    found = True
                    max_confidence = max(max_confidence, confidence)
                    cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 0), 3)
                    label = f"person {confidence:.0%}"
                    cv2.putText(
                        output,
                        label,
                        (x1, max(24, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2,
                        cv2.LINE_AA,
                    )
                if detections and max(item[1] for item in detections) >= max_confidence:
                    best_frame = output.copy()
                annotated.append(output)
                if found:
                    # One confirmed frame is enough to choose the GIF path.  Keep
                    # the remaining evidence frames but avoid needless inference.
                    annotated.extend(item.copy() for item in usable[frame_index + 1:])
                    break
        except Exception as exc:
            log.exception("Person inference failed (%s); sending evidence GIF", type(exc).__name__)
            self._net = None
            return PersonScan(False, False, usable)

        return PersonScan(True, found, annotated, max_confidence, best_frame)

    def _confirm_detections(self, frame: np.ndarray, candidates):
        confirmed = []
        for box, confidence in candidates:
            if confidence >= self._direct_confidence or self._has_face_near(frame, box):
                confirmed.append((box, confidence))
        return confirmed

    def _has_face_near(self, frame: np.ndarray, box: Tuple[int, int, int, int]) -> bool:
        """Require face evidence before accepting a low-confidence person box."""
        if self._face_detector is None:
            return False
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = box
        box_width = max(1, x2 - x1)
        box_height = max(1, y2 - y1)
        left = max(0, round(x1 - box_width * 0.4))
        right = min(width, round(x2 + box_width * 0.4))
        top = max(0, round(y1 - box_height * 0.6))
        bottom = min(height, round(y2 + box_height * 0.25))
        crop = frame[top:bottom, left:right]
        if crop.size == 0:
            return False
        resized = cv2.resize(crop, (320, 320), interpolation=cv2.INTER_AREA)
        try:
            _retval, faces = self._face_detector.detect(resized)
            return faces is not None and len(faces) > 0
        except cv2.error as exc:
            log.warning("Face confirmation failed (%s)", type(exc).__name__)
            self._face_detector = None
            return False

    def _infer_regions(
        self, frame: np.ndarray
    ) -> List[Tuple[Tuple[int, int, int, int], float]]:
        """Detect on the whole image and enlarged high-value regions."""
        height, width = frame.shape[:2]
        combined = []
        for x1_ratio, y1_ratio, x2_ratio, y2_ratio in self._regions:
            left = round(width * x1_ratio)
            top = round(height * y1_ratio)
            right = round(width * x2_ratio)
            bottom = round(height * y2_ratio)
            crop = frame[top:bottom, left:right]
            if crop.size == 0:
                continue
            for (x1, y1, x2, y2), confidence in self._infer(crop):
                combined.append(((x1 + left, y1 + top, x2 + left, y2 + top), confidence))

        if len(combined) <= 1:
            return combined

        boxes = [
            [x1, y1, max(1, x2 - x1), max(1, y2 - y1)]
            for (x1, y1, x2, y2), _confidence in combined
        ]
        scores = [confidence for _box, confidence in combined]
        indices = cv2.dnn.NMSBoxes(
            boxes, scores, self._confidence, self._nms_threshold
        )
        return [combined[int(index)] for index in np.asarray(indices).reshape(-1)]

    def _infer(self, frame: np.ndarray) -> List[Tuple[Tuple[int, int, int, int], float]]:
        input_image, letterbox = self._letterbox(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        normalized = (input_image.astype(np.float32) - self._mean) / self._std
        blob = cv2.dnn.blobFromImage(normalized)
        self._net.setInput(blob)
        outputs = self._net.forward(self._net.getUnconnectedOutLayersNames())

        # OpenCV 4.x can return these layers interleaved while the newer graph
        # engine groups all class layers before all box layers.  Identify them
        # by their stable channel counts instead of depending on list order.
        class_outputs = [output for output in outputs if output.shape[-1] == 80]
        box_outputs = [
            output
            for output in outputs
            if output.shape[-1] == 4 * (self._REG_MAX + 1)
        ]
        if len(class_outputs) != len(self._STRIDES) or len(box_outputs) != len(self._STRIDES):
            raise ValueError("Unexpected NanoDet output layout")
        all_boxes = []
        all_scores = []
        for stride, class_scores, box_prediction, anchors in zip(
            self._STRIDES, class_outputs, box_outputs, self._anchors
        ):
            class_scores = np.squeeze(class_scores, axis=0) if class_scores.ndim == 3 else class_scores
            box_prediction = np.squeeze(box_prediction, axis=0) if box_prediction.ndim == 3 else box_prediction

            logits = np.clip(box_prediction.reshape(-1, self._REG_MAX + 1), -20, 20)
            probabilities = np.exp(logits)
            probabilities /= np.sum(probabilities, axis=1, keepdims=True)
            distances = np.dot(probabilities, self._project).reshape(-1, 4) * stride

            if class_scores.shape[0] > 1000:
                top_indices = class_scores[:, 0].argsort()[::-1][:1000]
                class_scores = class_scores[top_indices]
                distances = distances[top_indices]
                anchors = anchors[top_indices]

            x1 = np.clip(anchors[:, 0] - distances[:, 0], 0, self._INPUT_SIZE[0])
            y1 = np.clip(anchors[:, 1] - distances[:, 1], 0, self._INPUT_SIZE[1])
            x2 = np.clip(anchors[:, 0] + distances[:, 2], 0, self._INPUT_SIZE[0])
            y2 = np.clip(anchors[:, 1] + distances[:, 3], 0, self._INPUT_SIZE[1])
            all_boxes.append(np.column_stack((x1, y1, x2, y2)))
            all_scores.append(class_scores[:, 0])

        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        xywh = boxes.copy()
        xywh[:, 2:4] -= xywh[:, 0:2]
        indices = cv2.dnn.NMSBoxes(
            xywh.tolist(), scores.tolist(), self._confidence, self._nms_threshold
        )

        detections = []
        for index in np.asarray(indices).reshape(-1):
            mapped = self._unletterbox(boxes[int(index)], frame.shape[:2], letterbox)
            detections.append((mapped, float(scores[int(index)])))
        return detections

    @classmethod
    def _letterbox(cls, image: np.ndarray):
        target_h, target_w = cls._INPUT_SIZE
        height, width = image.shape[:2]
        scale = min(target_w / width, target_h / height)
        new_w = max(1, round(width * scale))
        new_h = max(1, round(height * scale))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
        left = (target_w - new_w) // 2
        top = (target_h - new_h) // 2
        padded = cv2.copyMakeBorder(
            resized,
            top,
            target_h - new_h - top,
            left,
            target_w - new_w - left,
            cv2.BORDER_CONSTANT,
            value=0,
        )
        return padded, (top, left, new_h, new_w)

    @staticmethod
    def _unletterbox(box, original_shape, letterbox) -> Tuple[int, int, int, int]:
        height, width = original_shape
        top, left, new_h, new_w = letterbox
        x1 = max(0, round((box[0] - left) * width / new_w))
        y1 = max(0, round((box[1] - top) * height / new_h))
        x2 = min(width - 1, round((box[2] - left) * width / new_w))
        y2 = min(height - 1, round((box[3] - top) * height / new_h))
        return x1, y1, x2, y2

    @classmethod
    def _make_anchors(cls) -> List[np.ndarray]:
        anchors = []
        target_h, target_w = cls._INPUT_SIZE
        for stride in cls._STRIDES:
            feature_h = target_h // stride
            feature_w = target_w // stride
            x, y = np.meshgrid(np.arange(feature_w), np.arange(feature_h))
            centers_x = x.reshape(-1) * stride + 0.5 * (stride - 1)
            centers_y = y.reshape(-1) * stride + 0.5 * (stride - 1)
            anchors.append(np.column_stack((centers_x, centers_y)))
        return anchors

    @staticmethod
    def _validate_regions(regions) -> List[Tuple[float, float, float, float]]:
        valid = []
        for region in regions if isinstance(regions, list) else []:
            if not isinstance(region, (list, tuple)) or len(region) != 4:
                continue
            try:
                x1, y1, x2, y2 = (float(value) for value in region)
            except (TypeError, ValueError):
                continue
            if 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1:
                valid.append((x1, y1, x2, y2))
        return valid or [(0.0, 0.0, 1.0, 1.0)]
