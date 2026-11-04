# Object card: deer

> Worked example of `TEMPLATE.md`. Numbers in sections 2 and 7 were measured
> with the tools when this was written; the v0 model is the synthetic practice
> run from `ADDING_OBJECTS.md` step 0, so its numbers show how to read and use
> the results, not how well real deer will be found.

| | |
|---|---|
| **Status** | practice run done (synthetic); real data: collecting not started |
| **Owner** | (name) |
| **Started** | 2026-11-04 |
| **Current model version** | v0-practice (not deployed) |

---

## 1. Definition

| Field | Value |
|---|---|
| Class name | `deer` |
| One-line labelling rule | Any deer or fawn, standing or lying: box the whole visible body including head and legs, never the shadow. |
| Includes | Roe deer, red deer, fawns; standing, walking, lying; also dead animals on the ground |
| Excludes | Calves, sheep, goats (those are `livestock`); dogs; hay heaps and bushes, however deer-shaped |
| Looks like | `livestock` (calves, sheep), brown bushes, mole hills, hay heaps. In the practice data: `combine` and `harvester` (measured, section 7) |
| Why we want it | Find deer and fawns in a meadow just before it is mown, so they can be moved away |
| Worse mistake | **A miss** (an animal is killed); a false alarm only costs a walk to check. → keep the threshold as low as false alarms allow |

## 2. Can the camera see it? (go / no-go)

Camera: SIYI A2 mini, 1920×1080, 160° fisheye, pointed straight down by its
gimbal.

| Field | Value |
|---|---|
| Size seen from above | 1.5 × 0.5 m (roe deer; a fawn is smaller) |
| Planned mission altitude | first tried 100 m (the normal survey altitude), then 30 m |
| Model input / detection region / tiling | 320 / central half / 2×2 (default), then 640 / central half / 2×2 |
| Ground speed | 19 m/s, then 16 m/s (inside the 16-20 m/s cruise range) |
| Expected detection rate | 2 fps (to be measured with `preflight.py`) |

```text
$ python3 tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 100 --config config.yaml --fps 2
camera: 1920x1080, fisheye 160 deg; ground resolution at the centre: 14.5 cm per pixel at 100 m
1.5 x 0.5 m object: 10.3 x 3.4 px in the camera image, 6.9 x 2.3 px at the 320-px model input (from the 960x540-px region (2x2 tiles))
verdict: TOO SMALL - unlikely to work
in the detection region for ~4.4 s at 19 m/s (83 m of ground along track): ~8.7 detection frames at 2 fps

to reach ~10 px at the model input:
  imgsz  320, 2x2 tiles, region 50% of the width: fly at or below    23 m
  imgsz  640, 2x2 tiles, region 50% of the width: fly at or below    34 m

$ python3 tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 30 --roi 0.5 --tiles 2 --imgsz 640 --fps 2 --speed-mps 16
camera: 1920x1080, fisheye 160 deg; ground resolution at the centre: 4.4 cm per pixel at 30 m
1.5 x 0.5 m object: 34.4 x 11.5 px in the camera image, 34.4 x 11.5 px at the 640-px model input (from the 960x540-px region (2x2 tiles))
verdict: GOOD
note: each 480-px tile is enlarged to 640 px, which adds no detail - the camera's own 11.5 px is the limit
in the detection region for ~1.6 s at 16 m/s (25 m of ground along track): ~3.1 detection frames at 2 fps
```

| Result | Value |
|---|---|
| Pixels at the model input | 100 m, default setup: 6.9 × 2.3 (hopeless). 30 m, 640 px, 2×2 tiles: 34 × 11.5 |
| Seconds in the detection region / frames | 1.6 s; ~3 frames at 2 fps. `confirm.frames: 2` needs **at least ~1.3 fps**; aim for 2 |
| Verdict | **GO WITH CHANGES** |
| Changes needed | Separate **meadow missions at 30 m AGL, 16 m/s**; model trained and exported at **640** with the central half in **2×2 tiles** (4 runs of a 640-px model per frame); almost certainly needs a **Coral USB** - confirm with `preflight.py --alt-m 30 --speed-mps 16` |

The camera itself is the limit here: at 30 m it records the deer's 0.5 m width
as only 11.5 px, and above ~34 m fewer than 10, whatever the model does.

Mission planning at 30 m: the detection region covers ~50 m across track, so
survey lines ≤40 m apart. A 45° bank turn at 16 m/s has a ~26 m radius (52 m
across), wider than the line spacing: fly every other line and fill the gaps
on the way back. Meadows are often edged by 20-30 m trees: plan the entry and
exit legs above them. Keep within visual line of sight and the legal altitude
limit.

Other limits: **an ordinary camera cannot see a fawn hidden in tall grass.**
Fawn searches are normally done with thermal cameras early in the morning.
With this camera, expect to find deer in short or freshly mown grass and at
meadow edges, not hidden fawns. That use would need a thermal camera (a
hardware change, and a separate model).

## 3. Labelling rules

- [x] The box covers the whole visible animal: head, body, legs. Not the shadow.
- [x] Partly visible (image edge, tall grass, under trees): label it if the head or at least about a third of the body is visible.
- [x] Too small or blurry to tell it's a deer: skip.
- [x] Groups: one box per animal, even when they touch.
- [x] Shadows: never inside the box.
- [x] Every deer in every labelled image is labelled.

| Tricky case | Rule |
|---|---|
| Calf or sheep in a pasture | `livestock`, not `deer` |
| Dog in a field | Don't label (no class); keep such images, they are useful negatives |
| Person crouching in the grass | `person` |
| Two deer overlapping | Two boxes; each covers its own animal as far as you can tell |
| Deer at a forest edge, half under branches | Label if a third or more is visible; box only the visible part |

## 4. Data plan

| Target | First version | Reliable version |
|---|---|---|
| Labelled instances (training) | 300 | 1,500+ |
| Images containing it | 150 | 700+ |
| Flights | 4 meadows | 15+ meadows, 2 seasons |
| Validation flights | 1 | 3 |
| Held-out **test** flight, fully labelled | 1 meadow with known deer | 2 (different farms) |
| Look-alike images without it | 100 (pastures with calves, hay heaps, bushes) | 500+ |

| Source | Where it is | Images | Instances | Altitude | Licence OK? | Status |
|---|---|---|---|---|---|---|
| Own flights (`save_all_frames`, 30 m, 16 m/s) | `flights/flight_DATE/frames` | | | 30 m | yes | not started |
| Public aerial deer datasets (Roboflow Universe search "deer drone", "roe deer aerial") | `downloads/` | | | check | check each | not started |

Coverage:

- [ ] Early morning and midday light; low sun with long shadows
- [ ] Short grass, long grass, freshly mown rows; May and June (fawning and first mowing)
- [ ] Standing, walking, lying; partly in grass; at forest edges
- [ ] Deer near the image edges (wide lens)
- [ ] At 30 m AGL

Look-alikes to include as negatives: calves and sheep (labelled `livestock`),
hay heaps, brown bushes, mole hills, dogs, and images with combines and
harvesters (the confusion measured in section 7).

## 5. Acceptance criteria (decided before training)

| Metric | Measured on | Required |
|---|---|---|
| deer recall | held-out test flight | ≥ 0.85 |
| deer precision | held-out test flight | ≥ 0.80 |
| Old classes, mean mAP50 | validation set | no more than 0.03 below the previous model |
| False "deer" on a flight without deer | `eval_model.py` on that flight | ≤ 3 per flight |
| Detection rate on the aircraft | `preflight.py --alt-m 30 --speed-mps 16` | PASS (≥ ~1.3 fps measured, 640 px, 2×2 tiles) |

## 6. Commands

Run from `onboard/training/`.

```bash
# 6.1 can the camera see it
python3 ../tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 30 --roi 0.5 --tiles 2 --imgsz 640 --fps 2 --speed-mps 16

# 6.2 classes.yaml, appended at the end:
#   15: deer              # any deer or fawn, whole visible body, never the shadow

# 6.3 draft labels per meadow flight; one fully labelled meadow held out as test
python3 prelabel.py --weights runs/detect/farm_v1/weights/best.pt \
    --images flights/flight_0528_meadowA/frames --out to_label/flight_0528_meadowA \
    --add-class deer --imgsz 640
python3 prelabel.py --weights runs/detect/farm_v1/weights/best.pt \
    --images flights/flight_0604_meadowD/frames --out to_label/test/flight_0604_meadowD \
    --add-class deer --imgsz 640

# 6.5 merge with everything farm_v1 was trained on
python3 merge_datasets.py --out datasets/merged_v2 --overwrite \
    datasets/merged_v1 to_label downloads/roboflow_deer

# 6.6 check at the size we will train at
python3 check_dataset.py datasets/merged_v2/data.yaml --imgsz 640 --preview 24

# 6.7 train at 640 (the deer mission's input size) from the current model
python3 train.py --data datasets/merged_v2/data.yaml \
    --model runs/detect/farm_v1/weights/best.pt \
    --epochs 100 --imgsz 640 --device 0 --name deer_v2

# 6.8 measure against section 5
yolo val model=runs/detect/deer_v2/weights/best.pt \
    data=datasets/merged_v2/data.yaml split=test imgsz=640
python3 export.py --weights runs/detect/deer_v2/weights/best.pt --imgsz 640 \
    --model-name farm_v2 --edgetpu
python3 ../tools/threshold_sweep.py --model ../models/farm_v2.tflite \
    --labels ../models/farm_v2_labels.txt --data datasets/merged_v2/data.yaml \
    --split test --classes deer
python3 ../tools/eval_model.py --model ../models/farm_v2.tflite \
    --labels ../models/farm_v2_labels.txt --format yolo \
    --images flights/flight_0611_no_deer/frames --out check_v2 --score 0.55
```

## 7. Results log

| Version | Date | Train instances of deer | deer P / R / mAP50 (test flight) | Old classes mean mAP50 (val) | False "deer" without deer | Meets section 5? | Deployed? | Notes |
|---|---|---|---|---|---|---|---|---|
| previous (15 classes, `synth_proof`) | 2026-11-03 | 0 | - | 0.823 | - | - | - | The baseline v0 is compared against, on the same validation images |
| v0-practice | 2026-11-04 | 354 (synthetic) | 1.00 / 0.93 / 0.98 | 0.969 | 12 in 70 images at threshold 0.55 (17 at 0.35) | **No**: false alarms | No | Practice data. False deer were really combines (9) and harvesters (3): the practice generator draws deer in almost the same colour |
| deer-only experiment | 2026-11-04 | 354 (synthetic) | not measured | **0.066** | not measured | No | No | Trained on deer photos only: forgot the other classes (`ADDING_OBJECTS.md` step 7) |

How v0's threshold was chosen (`threshold_sweep.py` on the validation set,
48 labelled deer among 86 images of all classes):

| deer threshold | precision | recall | false deer |
|---|---|---|---|
| 0.35 | 0.66 | 0.88 | 22 |
| 0.55 | 0.74 | 0.83 | 14 |
| 0.65 | 0.81 | 0.79 | 9 |
| 0.75 | 0.86 | 0.52 | 4 |

A miss is the worse mistake here (section 1), so 0.75 throws away too many
deer. 0.55-0.65 is the range to choose from, but at either the false alarms
still break section 5. The fix is in the data, not the threshold: add the
look-alikes (here combines and harvesters next to deer) to the next version.

## 8. Deployment

Not deployed. When a version passes section 5:

```yaml
detector:
  model: models/farm_v2_edgetpu.tflite     # Coral needed (section 2)
  labels: models/farm_v2_labels.txt
  format: yolo
  roi: [0.25, 0.25, 0.75, 0.75]            # central half of the fisheye image
  tiled: true
  tile_grid: [2, 2]
  class_thresholds:
    deer: 0.55                             # from threshold_sweep.py on the test flight
targets:
  - deer
confirm:
  frames: 2                                # 2 sightings within ~1.6 s in the region at 30 m
```

Meadow missions only (30 m, 16 m/s); the normal 100 m survey keeps `farm_v1`.
Previous model kept for rollback: `models/farm_v1.tflite` + labels.

## 9. Sign-off

- [x] Section 2 verdict is GO WITH CHANGES (changes listed; Coral not bought yet)
- [x] Section 5 criteria written down before training
- [x] `check_dataset.py`: no errors; preview images look right (v0)
- [ ] Held-out test flight results meet section 5 (v0 fails on false alarms)
- [x] Old classes did not get worse than allowed (v0: 0.969 vs 0.823 before; better, partly because the old practice data had loose boxes)
- [x] Results log filled in, including the rejected models
- [ ] Bench test on the Pi with Coral; fps measured
- [ ] Previous model kept for rollback
- [ ] First meadow flight with the new model reviewed
