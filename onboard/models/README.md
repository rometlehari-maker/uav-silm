# Models

`pi/install.sh` downloads the standard model onto the Pi (it is not
committed). One ready-made farm model **is** committed: `silm_oi` (below).
Models are sent to the Pi with the settings from the Ground Station (they live
in `~/silm-settings/models/` there).

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

## Ready-made farm model: `silm_oi` (tractor, car, truck, livestock)

`silm_oi.tflite` + `silm_oi_labels.txt` + `silm_oi.json`: YOLO11n trained on
~1,800 real photos from Google Open Images (`training/openimages_dataset.py`),
320×320 input, network INT8 with the detection head in float (4.4 MB). The
Ground Station offers it on the Camera tab as **"Ready-made: Farm vehicles and
livestock"** and sends it to the Pi like a model you trained.

- Measured precision/recall: [`training/README.md`](../training/README.md#results-the-ready-made-model-silm_oi).
- **Trained on ground-level photos**: from straight above it recognises much
  less. Treat it as a starting point; retrain with your own aerial pictures
  (tab 9).
- It does **not** know combines (no usable public photos) or the other
  classes in `classes.yaml`; the labels file lists all 15 names only so the
  class numbers match.
- **Licence**: the weights are derived from Ultralytics YOLO11n, which is
  AGPL-3.0 (or an Ultralytics enterprise licence); the training photos are
  CC BY 2.0 and the boxes CC BY 4.0 (Open Images). Check this fits how you
  share the software.

## Your own model (tractor, combine, ...)

Trained with the Ground Station's tab 9 or `training/` (YOLO, exported to a
`.tflite` with an INT8 body and float detection head + `<name>_labels.txt`). In the settings it is
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
