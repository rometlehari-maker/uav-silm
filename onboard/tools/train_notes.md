# Training a custom detector for harvesters / combines (and anything else)

COCO already gives you `car`, `truck`, `person`, `boat`. Agricultural machinery
(`harvester`, `combine`) and good aerial `fire`/`smoke` are not in COCO, so you
train a small model and drop it in. The onboard pipeline is class-name driven —
no code changes, just new files + config.

## 1. Data

- Collect aerial/oblique images of the targets at realistic altitude and angle
  (ideally from your own camera at ~80–120 m). A few hundred boxed instances per
  class is enough for a usable student-grade model; more is better.
- Useful public starting points to supplement your own data:
  - Agriculture/vehicle sets on Roboflow Universe (search "combine harvester",
    "agricultural machinery aerial").
  - Wildfire sets: FLAME, D-Fire, "wildfire smoke" datasets for `fire`/`smoke`.
- Label with the classes you'll put in `targets`, e.g. `harvester`, `combine`,
  `tractor`. Keep names identical to the labels file.

## 2. Train (transfer learning)

Fastest route to something that runs on a Pi 3 / Coral:

- **Option A — Ultralytics YOLO (easy):** train `yolov8n`/`yolo11n` on your set,
  then export to a quantised TFLite:
  ```bash
  yolo detect train model=yolo11n.pt data=combine.yaml imgsz=320 epochs=100
  yolo export model=runs/detect/train/weights/best.pt format=tflite int8=True imgsz=320
  ```
  Note: YOLO TFLite output tensors differ from SSD. Use the SSD/MobileNet route
  below for drop-in compatibility with `detector.py`, or adapt `_read_outputs`.

- **Option B — SSD-MobileNet-V2 (drop-in):** fine-tune SSD-MobileNet-V2 320x320
  with the TF Object Detection API or `tflite-model-maker`, which exports a model
  with the standard 4-tensor detection post-process that `detector.py` already
  handles:
  ```python
  from tflite_model_maker import object_detector, model_spec
  spec = model_spec.get('efficientdet_lite0')  # or a mobilenet-ssd spec
  data = object_detector.DataLoader.from_pascal_voc('images','ann', ['harvester','combine','tractor'])
  model = object_detector.create(data, model_spec=spec, epochs=50)
  model.export(export_dir='.', tflite_filename='combine_quant.tflite')
  ```

## 3. (Optional) compile for Coral

```bash
edgetpu_compiler combine_quant.tflite   # -> combine_quant_edgetpu.tflite
```

## 4. Install

```
onboard/models/combine_quant.tflite        # or _edgetpu.tflite
onboard/models/combine_labels.txt          # one class per line, matching training
```
```yaml
# config.yaml
detector:
  model: models/combine_quant.tflite
  labels: models/combine_labels.txt
targets: [harvester, combine, tractor, car, truck]
```

## Reality check on the Pi 3

- Input size matters most. 320×320 int8 SSD-MobileNet runs ~1–3 FPS on a Pi 3
  CPU. Keep inputs small; raise `activation.detect_every_n_frames` if needed.
- Fly high enough and/or slow enough that a target stays in frame across a few
  frames, so a 1–2 FPS detector still catches it.
- A $60 Coral USB stick takes the same model to 30+ FPS. The code uses it
  automatically if an `_edgetpu.tflite` model is present and the stick is in.
