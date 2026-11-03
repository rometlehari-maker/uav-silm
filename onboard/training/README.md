# Training the SILM ag/forestry detector

This folder trains the object detector that runs on the aircraft. The pipeline
is: **get labelled data → `train.py` → `export.py` → drop the `.tflite` into
`../models/` → set `config.yaml`**. The onboard code already knows how to run a
YOLO model (`detector.format: yolo`), so no app code changes are needed.

> Honesty up front: a model that works on *real* aerial footage needs *real*
> labelled aerial images — hundreds to thousands of boxes per class, from an
> altitude and angle like your own camera's. The synthetic generator here only
> proves the pipeline end to end; it will not recognise a real combine.

## Will it work in real situations? (read this)

Real-world performance is decided almost entirely by **data**, not by the code.
To get a detector that likely works over your fields:

1. **Match the viewpoint.** Train on images taken from roughly your flight
   altitude and camera angle (near-nadir / oblique from 80–120 m). A model
   trained on ground-level photos will largely fail top-down. Your own saved
   flight frames (`storage.save_all_frames: true`) are the best data you can
   get — use the fly → label → retrain loop below.
2. **Enough variety.** Different seasons, crops, light, and each target from
   several angles. Aim for hundreds of instances per class to start, thousands
   for the ones that matter most.
3. **Use real public data to bootstrap** (`fetch_real_data.py`): Roboflow
   Universe has real *drone/aerial* agriculture, vehicle and wildfire datasets
   (recommended — right viewpoint); Open Images adds bulk real images for
   car/truck/tractor/cattle (ground-level — mix with aerial, don't rely on it).
4. **Tile at inference** for small objects (`detector.tiled: true`) — the single
   biggest real-world recall win, best paired with a Coral.
5. **Expect to iterate.** The first model will miss things. The fly→label→
   retrain loop is what converges it to real-world-usable on *your* ground.

No one — including me — can promise field accuracy from code alone; these steps
are how you actually get there, and how you measure whether you're there yet
(the mAP/confusion-matrix metrics after each run).

## Classes

Defined once in `classes.yaml` (person, car, truck, tractor, combine, harvester,
baler, hay_bale, livestock, silo, barn, logging_truck, timber_stack, fire,
smoke). Edit that list *before* you start labelling and keep it stable.

## Option A — Prove the pipeline now (synthetic, CPU)

```bash
cd onboard/training
python3 make_synthetic.py --out datasets/synth --n-train 350 --n-val 70
python3 train.py --data datasets/synth/data.yaml --model yolo11n.pt \
    --epochs 12 --imgsz 256 --device cpu --name synth_proof
python3 export.py --weights runs/detect/synth_proof/weights/best.pt \
    --imgsz 256 --model-name silm_synth
# -> ../models/silm_synth.tflite  and  ../models/silm_synth_labels.txt
```
Then verify it actually runs in the onboard detector:
```bash
cd ..
python3 tools/eval_model.py --model models/silm_synth.tflite \
    --labels models/silm_synth_labels.txt --format yolo \
    --images training/datasets/synth/images/val
```

## Option B — A real model (your data, GPU machine)

1. **Collect** aerial images of the targets from a similar altitude/angle to
   your camera (ideally footage from your own AHD camera). Variety matters:
   seasons, lighting, crop types, angles.
2. **Label** them in YOLO format. Easiest routes:
   - **Roboflow** (web): upload, draw boxes, "Export → YOLOv11 / YOLOv8 PyTorch",
     which gives you an `images/` + `labels/` tree and a `data.yaml`.
   - **CVAT** or **labelImg** (offline) also export YOLO txt labels.
   - Supplement with public sets (Roboflow Universe: "combine harvester aerial",
     "agriculture vehicles"; wildfire: FLAME, D-Fire). Re-map their class indices
     to match `classes.yaml`.
3. **Point** a `data.yaml` at the data (see `farm.data.yaml.example`).
4. **Train** on a GPU box (10–100× faster than this CPU):
   ```bash
   python3 train.py --data datasets/farm/data.yaml --model yolo11s.pt \
       --epochs 150 --imgsz 640 --batch 32 --device 0 --name farm_v1
   ```
5. **Read the metrics** Ultralytics prints and writes to
   `runs/detect/farm_v1/`: look at `mAP50`, `mAP50-95`, the PR curve and the
   confusion matrix (`confusion_matrix.png`). Low recall on a class = collect
   more examples of it.
6. **Export** for the Pi (and Coral if you have one):
   ```bash
   python3 export.py --weights runs/detect/farm_v1/weights/best.pt \
       --imgsz 320 --model-name silm_farm            # add --edgetpu for Coral
   ```
7. **Deploy**: copy `../models/silm_farm.tflite` + `_labels.txt` to the Pi, set
   `config.yaml` (`detector.model/labels/format: yolo`), bench-test, fly.

## Iterating (the loop you'll actually live in)

1. Fly a mission; the aircraft saves detections **and** full frames to the SD
   card (set `storage.save_all_frames` if you want every frame, or mine the
   saved detections).
2. Pull the card. The frames where the model was wrong (missed a combine, called
   a hay bale a car) are your best new training data.
3. Label those, add them to the dataset, re-run `train.py` (use `--model
   <previous best.pt>` to continue from where you were), re-export, re-fly.
4. Each loop the model gets better on *your* fields specifically. This
   "fly → label the mistakes → retrain" loop beats any amount of generic data.

## Picking a base model

| Base | Speed on Pi 3 CPU | Use when |
|------|-------------------|----------|
| `yolo11n` / `yolov8n` | ~1–3 FPS @ 256–320 | Pi 3, no Coral (your case) |
| `yolo11s` | slow on CPU | only with a Coral stick, or a stronger companion |

Keep `--imgsz` small (256–320) for the Pi. Train at the size you'll deploy.
