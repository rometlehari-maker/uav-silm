# Training the SILM ag/forestry detector

> **Adding a new object (e.g. deer, excavators, irrigation pivots)?** Follow
> [`ADDING_OBJECTS.md`](ADDING_OBJECTS.md): a step-by-step tutorial covering
> whether the camera can see it, labelling, merging datasets, retraining,
> checking the results and deploying, with a 10-minute practice run. Keep an
> object card per object: [`objects/TEMPLATE.md`](objects/TEMPLATE.md)
> (filled-in example: [`objects/deer.md`](objects/deer.md)).

This folder trains the object detector that runs on the aircraft. The pipeline
is: **get labelled data → `train.py` → `export.py` → drop the `.tflite` into
`../models/` → set `config.yaml`**. The onboard code already knows how to run a
YOLO model (`detector.format: yolo`), so no app code changes are needed.

> Honesty up front: a model that works on *real* aerial footage needs *real*
> labelled aerial images — hundreds to thousands of boxes per class, from an
> altitude and angle like your own camera's. The synthetic generator here only
> proves the pipeline end to end; it will not recognise a real combine.

## The easy way: the Ground Station's "Teach new objects" tab

Everything below is also behind tab 9 of the laptop Ground Station
([`../GROUND_STATION.md`](../GROUND_STATION.md#9--teach-new-objects)): add
pictures, drag a box around each object, press **Start training**, then **Use
on the aircraft** and send it. It writes the same YOLO dataset format and runs
`train.py` and `export.py` for you. Use the command line below for big
datasets, a GPU machine, or to understand what happens.

## Real photos to start from: Open Images (no account needed)

```bash
cd onboard/training
python3 -m pip install ultralytics litert-torch    # once (~2 GB)
python3 openimages_dataset.py --per-class 450 --out datasets/openimages
python3 train.py --data datasets/openimages/data.yaml --model yolo11n.pt \
    --epochs 40 --imgsz 320 --device cpu --name oi_v1
python3 export.py --weights runs/detect/oi_v1/weights/best.pt --imgsz 320 \
    --model-name silm_oi --data datasets/openimages/data.yaml
```

`openimages_dataset.py` streams Google Open Images' public annotation lists
(once, ~5 GB streamed, a few MB kept), picks photos with **real human-drawn
boxes** - car, truck, livestock (cattle/horse/sheep) - and photos people
verified as showing a **tractor**, whose vehicle boxes become tractor boxes,
downloads them (~1,800 photos, ~150 MB) and writes a YOLO dataset with the
class numbers of `classes.yaml`. What I checked when building it:

- **tractor**: about 8 in 10 boxes are good (some wagons, toy models and
  close-ups slip in; some tractors in busy photos are unboxed).
- **combine is left out**: Open Images' "Harvester" photos are mostly tractors
  with implements, rakes and sprayers - training "combine" on them would teach
  the wrong thing. Combines need your own pictures.
- **Viewpoint**: these are mostly **ground-level** photos. The model learns the
  objects, but recognises them from straight above much less well. The
  ready-made model therefore adds drone pictures of cattle and sheep (below);
  add your own aerial pictures (ordinary pictures saved in flight) and retrain.
- **Livestock** photos are mostly horses (many with riders), some sheep and
  cattle.

## Results: the ready-made model `silm_oi` (version 2)

Made in two steps, both on CPU at 320 px from yolo11n:

1. **Open Images** (above): 40 epochs, ~50 min (`runs/oi_v1`).
2. **Drone pictures added** (`aerial_v2`, November 2026): 30 more epochs from
   step 1's weights (~2.5 h), on Open Images plus cattle and sheep seen from a
   drone, taken from Roboflow Universe:
   - [aerial-cattle-detection](https://universe.roboflow.com/cattle-psyty/aerial-cattle-detection)
     by cattle-psyty, CC BY 4.0: 640 px drone pictures over pasture (720 train / 209 check);
   - [counting-cattle](https://universe.roboflow.com/arthurmessias/counting-cattle)
     by arthurmessias, CC BY 4.0: straight-down feedlot pictures, cut into
     640 px tiles with `tile_dataset.py` (1,500 / 300 tiles sampled);
   - [aerial-sheep](https://universe.roboflow.com/riis/aerial-sheep) by riis,
     Public Domain: 4K drone video frames, tiled (1,500 / 300 tiles sampled).

   Their test parts were kept apart for measuring. The exact file list and
   every setting are in `manifests/aerial_v2.txt`: copy each set into your
   Roboflow workspace, export it as YOLOv8, tile the big ones, then
   `merge_datasets.py` with `--alias Cow|cow|0|Lying-Cow|Standing-Cow|sheep=livestock`,
   and `train.py --model runs/oi_v1/weights/best.pt --epochs 30 --imgsz 320 --batch 32`.

Exported with `export.py` and **committed** as `../models/silm_oi.tflite`
(4.4 MB). The Ground Station offers it as "Ready-made: Farm vehicles and
livestock".

**From above**, measured with `../tools/eval_detections.py` (the aircraft's own
detector code, 2x2 tiles as in flight, score 0.35, boxes of 8 px or more,
box must overlap the true one by at least 50 %) on pictures never used for
training. *Precision* = of the boxes it reported, how many were right;
*recall* = of the real objects, how many it found.

| Pictures (objects) | version 1 | **version 2** |
|---|---|---|
| drone over pasture, cattle (2,272) | 0.71 / 0.05 | **0.79 / 0.77** |
| feedlot from above, cattle (8,355) | 0.51 / 0.26 | **0.75 / 0.92** |
| drone over pasture, sheep (3,491) | 0.56 / 0.15 | **0.62 / 0.93** |
| cars from above (COWC tiles, whole picture, 1,139) | 0.44 / 0.01 | 0.60 / 0.05 |
| VisDrone cars (13,671) | 0.84 / 0.13 | 0.80 / 0.14 |
| VisDrone trucks (683) | 0.24 / 0.01 | 0.24 / 0.01 |

(precision / recall). VisDrone and the COWC tiles are for measuring only: their
licences don't allow training a model we publish (`visdrone_to_yolo.py`).

**From the ground**, on the 298 held-out Open Images photos (whole picture):

| Object (boxes) | version 1 at 0.35 | **version 2 at 0.35** | standard model at 0.35 |
|---|---|---|---|
| tractor (106) | 0.73 / 0.38 | **0.71 / 0.35** | - / 0.00 (doesn't know tractors) |
| car (232) | 0.72 / 0.37 | **0.67 / 0.36** | 0.21 / 0.45 |
| truck (125) | 0.70 / 0.58 | **0.69 / 0.50** | 0.30 / 0.51 |
| livestock (153) | 0.75 / 0.52 | **0.76 / 0.39** | 0.51 / 0.46 (cow+sheep+horse) |

Ultralytics' own validation of version 2 (both kinds of pictures): mAP50 0.56,
mAP50-95 0.33.

What this means:
- **Cattle and sheep from above**: it now finds 8 to 9 in 10, and 6 to 8 in 10
  of its boxes are right (sheep has the most false alarms).
- **Tractors, cars and trucks** were only ever shown from the ground: from
  above it still finds few of them. No public top-down set with a usable
  licence was found for them (the candidates checked are listed in
  `../HANDOFF.md`). Teach them with your own flight pictures (tab 9).
- From the ground it got somewhat worse (livestock recall 0.52 -> 0.39), the
  price of learning the top-down view. The aircraft looks straight down.
- **Speed**: same network as version 1: 12 ms per picture on the cloud test
  machine's CPU. Measure it on the Pi itself with `tools/preflight.py`.
- **Export fix found on the way**: Ultralytics 8.4's fully-INT8 export pinned
  every score at 0.50 (all thresholds useless). `export.py` keeps the
  detection head in float and checks the scores against the original.
- `tests/test_bundled_model.py` re-checks the model on four ground photos
  (`tests/data/oi_samples/`, with their authors and licences), plus that a
  blank picture gives no detections.

To measure a model from above yourself:

```bash
cd .. && python3 tools/eval_detections.py --model models/silm_oi.tflite \
    --labels models/silm_oi_labels.txt --images DIR/test/images --gt DIR/test/labels \
    --names DIR/names.txt --classes livestock --grid 2 --min-px 8
```

## Will it work in real situations? (read this)

Real-world performance is decided almost entirely by **data**, not by the code.
To get a detector that likely works over your fields:

1. **Match the viewpoint.** Train on images taken from roughly your flight
   altitude and camera angle (near-nadir, at the altitude you will fly; check the
   object is big enough there with `../tools/pixels_on_target.py`). A model
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
   your camera (ideally footage from your own SIYI A2 mini). Variety matters:
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
