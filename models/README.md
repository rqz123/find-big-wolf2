# Person detection model

The two local inference models are distributed by the OpenCV Model Zoo:

- [`person_detection_nanodet_2022nov.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet): NanoDet-m-plus-1.5x 416, Apache License 2.0.
- [`face_detection_yunet_2023mar.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet): YuNet face detector, MIT License.
- [`face_recognition_sface_2021dec.onnx`](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface): SFace face recognizer used to recognize the owner, Apache License 2.0.

- Purpose: local COCO `person` detection; camera frames are not sent to a cloud inference service.
- NanoDet upstream file: `object_detection_nanodet_2022nov.onnx`
- NanoDet SHA-256: `4B82DA9944B88577175EE23A459DCE2E26E6E4BE573DEF65B1055DC2D9720186`
- YuNet SHA-256: `8F2383E4DD3CFBB4553EA8718107FC0423210DC964F9F4280604804ED2552FA4`
- SFace SHA-256: `0BA9FBFA01B5270C96627C4EF784DA859931E02F04419C829E83484087C34E79`
- Licenses: [LICENSE-NANODET.txt](LICENSE-NANODET.txt), [LICENSE-YUNET.txt](LICENSE-YUNET.txt) and [LICENSE-SFACE.txt](LICENSE-SFACE.txt).
