from __future__ import annotations

import unittest
from unittest.mock import Mock

import numpy as np

from core.person_detector import PersonDetector


class PersonDetectorConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = object.__new__(PersonDetector)
        self.detector._direct_confidence = 0.50
        self.detector._has_face_near = Mock(return_value=False)
        self.frame = np.zeros((20, 30, 3), dtype=np.uint8)
        self.box = (1, 2, 10, 18)

    def test_high_confidence_person_does_not_require_face(self) -> None:
        confirmed = self.detector._confirm_detections(
            self.frame, [(self.box, 0.75)]
        )

        self.assertEqual(confirmed, [(self.box, 0.75)])
        self.detector._has_face_near.assert_not_called()

    def test_low_confidence_person_requires_face(self) -> None:
        self.detector._has_face_near.return_value = True

        confirmed = self.detector._confirm_detections(
            self.frame, [(self.box, 0.35)]
        )

        self.assertEqual(confirmed, [(self.box, 0.35)])

    def test_low_confidence_chair_candidate_is_rejected_without_face(self) -> None:
        confirmed = self.detector._confirm_detections(
            self.frame, [(self.box, 0.45)]
        )

        self.assertEqual(confirmed, [])


if __name__ == "__main__":
    unittest.main()
