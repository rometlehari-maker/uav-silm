# Models

The `.tflite` files are **not committed** (binary). `pi/install.sh` downloads
the standard model onto the Pi; trained models come from the Ground Station
(tab 9) and are sent to the Pi with the settings (they live in
`~/silm-settings/models/` there).

## Standard model: COCO SSD MobileNet v1 (quantised)

Recognises 80 everyday object types, among them person, car, truck, bus, boat,
cattle (`cow`), horse, sheep. Runs on the Pi 4's CPU.

```bash
cd onboard/models
curl -fsSL -o /tmp/coco.zip \
  https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
echo "a809cd290b4d6a2e8a9d5dad076e0bd695b8091974e0eed1052b480b2f21b6dc  /tmp/coco.zip" | sha256sum -c -
unzip -p /tmp/coco.zip detect.tflite > coco_ssd_mobilenet_v1_quant.tflite
```

`coco_labels.txt` (committed) matches this model's class numbers: 90 lines,
COCO's ids 0-89, with `???` at the unused ids. Don't replace it with an
80-line COCO list: every class after the first gap would get the wrong name.

## Your own model (tractor, combine, ...)

Trained with the Ground Station's tab 9 or `training/` (YOLO, exported to an
INT8 `.tflite` + `<name>_labels.txt`). In the settings it is
`detector.format: yolo`; the Ground Station sets this when you press **Use on
the aircraft**.

## Coral USB accelerator (optional)

An `*_edgetpu.tflite` model is used with a Coral stick automatically
(`training/export.py --edgetpu`). On the Pi 4 the CPU is usually fast enough;
`tools/preflight.py` measures it.

## Check a model with its labels

```bash
python3 - <<'PY'
from silm_recon.detector import ObjectDetector
d = ObjectDetector("models/coco_ssd_mobilenet_v1_quant.tflite", "models/coco_labels.txt", 0.3)
print("input size:", d.input_size, "format:", d.fmt, "labels:", len(d.labels))
PY
```
