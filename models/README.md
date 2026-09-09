# Person detection model

The two local inference models are distributed by the OpenCV Model Zoo:

- [`person_detection_nanodet_2022nov.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet): NanoDet-m-plus-1.5x 416, Apache License 2.0.
- [`face_detection_yunet_2023mar.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet): YuNet face detector, MIT License.

- Purpose: local COCO `person` detection; camera frames are not sent to a cloud inference service.
- NanoDet upstream file: `object_detection_nanodet_2022nov.onnx`
- NanoDet SHA-256: `4B82DA9944B88577175EE23A459DCE2E26E6E4BE573DEF65B1055DC2D9720186`
- YuNet SHA-256: `8F2383E4DD3CFBB4553EA8718107FC0423210DC964F9F4280604804ED2552FA4`
- Licenses: [LICENSE-NANODET.txt](LICENSE-NANODET.txt) and [LICENSE-YUNET.txt](LICENSE-YUNET.txt).
