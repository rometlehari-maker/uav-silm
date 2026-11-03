# Models

The `.tflite` weight files are **not committed** (binary, and easy to fetch).
Download them onto the Pi once, while it still has internet on the bench.

## Bundled baseline: SSD-MobileNet-v2, COCO (gives you `car`, `truck`, `person`...)

CPU model (runs on the Pi 3 with no accelerator):

```bash
cd onboard/models
# Quantised COCO SSD MobileNet v2 with the detection post-process op:
wget -O ssd_mobilenet_v2_coco_quant.tflite \
  https://storage.googleapis.com/download.tensorflow.org/models/tflite/coco_ssd_mobilenet_v1_1.0_quant_2018_06_29.zip
# (unzip and rename detect.tflite -> ssd_mobilenet_v2_coco_quant.tflite,
#  and labelmap.txt -> coco_labels.txt if you prefer the shipped labels)
```

Edge-TPU model (only if you add a Coral USB stick — optional, makes it ~30 FPS):

```bash
wget -O ssd_mobilenet_v2_coco_quant_edgetpu.tflite \
  https://github.com/google-coral/test_data/raw/master/ssd_mobilenet_v2_coco_quant_postprocess_edgetpu.tflite
```
Then point `detector.model` at the `_edgetpu.tflite` file — the code loads the
Coral delegate automatically.

## Custom targets: `harvester`, `combine`

These are **not** COCO classes. To recognise them you need a small custom model.
See `../tools/train_notes.md` for the transfer-learning recipe. Export a
`*_quant.tflite` (and optionally a `*_edgetpu.tflite`), put it here, write its
class names into a labels file, and set `detector.model` / `detector.labels` /
`targets` in `config.yaml`. No pipeline code changes are needed.

## Checking a model matches its labels

```bash
python3 - <<'PY'
from silm_recon.detector import ObjectDetector
d = ObjectDetector("models/ssd_mobilenet_v2_coco_quant.tflite", "models/coco_labels.txt", 0.3)
print("input size:", d.input_size, "labels:", len(d.labels))
PY
```
