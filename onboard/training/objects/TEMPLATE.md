# Object card: OBJECT

> **How to use:** copy this file to `training/objects/your_class_name.md`, fill
> it in top to bottom, and keep it updated as you go. Sections 1-5 are decided
> **before** labelling or training; 6-9 record what you did and what you got.
> The full explanation of every step is in `training/ADDING_OBJECTS.md`, and a
> filled-in example is `training/objects/deer.md`.
>
> In the commands, replace `OBJECT` with the class name and the folder names
> with yours.

| | |
|---|---|
| **Status** | idea / collecting / labelling / training / deployed / dropped |
| **Owner** | |
| **Started** | |
| **Current model version** | |

---

## 1. Definition

| Field | Value |
|---|---|
| Class name (lowercase_with_underscores; never renamed later) | |
| One-line labelling rule | |
| Includes | |
| Excludes (never label as this class) | |
| Looks like (other classes or objects it can be confused with) | |
| Why we want it (what decision a detection supports) | |
| Worse mistake: a miss or a false alarm? | → threshold lower (misses worse) / higher (false alarms worse) |

## 2. Can the camera see it? (go / no-go)

Run `python3 tools/pixels_on_target.py --object-m W --length-m L --alt-m A --tiles T --imgsz I --fps F --speed-mps S`
from `onboard/` with your numbers, and paste the output.

| Field | Value |
|---|---|
| Size seen from above, length × width (m) | |
| Planned mission altitude (m AGL) | |
| Model input size / tiling / accelerator | 320 / off / none (Pi 3 default) |
| Ground speed (m/s) | |
| Expected detection frames per second on the aircraft | |

```text
(paste the pixels_on_target.py output here)
```

| Result | Value |
|---|---|
| Pixels at the model input, length × width | |
| Seconds in view / detection frames per object | |
| Verdict | GO / GO WITH CHANGES / NO-GO |
| Changes needed (altitude, tiling, model size, Coral, speed, mission type) | |

Other limits (lighting, hidden under vegetation, season, legal limits on
altitude or location):

## 3. Labelling rules

Everyone who labels follows these. Decide them now, and add tricky cases as they come up.

- [ ] The box covers:
- [ ] Partly visible at the image edge or behind something: label if
- [ ] Too small or blurry to identify: label / skip
- [ ] Groups (several close together): one box per individual / one box per group
- [ ] Shadows, reflections: never inside the box
- [ ] Every instance in every labelled image is labelled (unlabelled ones teach "not OBJECT")

| Tricky case | Rule |
|---|---|
| | |
| | |
| | |

## 4. Data plan

| Target | First version | Reliable version |
|---|---|---|
| Labelled instances (training) | | |
| Images containing it | | |
| Flights it was collected on | | |
| Validation flights (whole flights) | | |
| Held-out **test** flight, fully labelled, never trained on | | |
| Look-alike images without it (negatives) | | |

| Source | Where it is | Images | Instances | Altitude | Licence OK? | Status |
|---|---|---|---|---|---|---|
| Own flights (`save_all_frames`) | | | | | yes | |
| | | | | | | |

Coverage (tick when the data has it):

- [ ] Different times of day / light (sun, overcast, low sun with long shadows)
- [ ] Different seasons / crop states / backgrounds
- [ ] Different poses / orientations / partly hidden
- [ ] Across the whole image, including the edges (wide lens distorts there)
- [ ] At the planned mission altitude

Look-alikes to include as negatives:

## 5. Acceptance criteria (decide BEFORE training)

| Metric | Measured on | Required | How |
|---|---|---|---|
| OBJECT recall | held-out test flight | ≥ | `yolo val ... split=test` (step 6.8) |
| OBJECT precision | held-out test flight | ≥ | same |
| Old classes, mean mAP50 | validation set | no more than ___ below the previous model | training output / step 6.8 |
| False alarms on a flight without OBJECT | `eval_model.py` on that flight | ≤ ___ per flight | step 6.8 |
| Detection rate on the aircraft | Pi bench test, `health.ndjson` fps | ≥ ___ fps (from section 2) | step 6.9 |

## 6. Commands

Run from `onboard/training/` unless the line says otherwise.

```bash
# 6.1 can the camera see it
python3 ../tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 30 --tiles 2 --imgsz 640 --fps 2

# 6.2 add the class: append ONE line at the end of classes.yaml, next number
#     e.g.   15: OBJECT              # one-line labelling rule

# 6.3 draft labels for a flight's frames (repeat per flight; one folder per flight)
python3 prelabel.py --weights runs/detect/CURRENT_MODEL/weights/best.pt \
    --images flights/flight_DATE/frames --out to_label/flight_DATE --add-class OBJECT
#     hold one fully labelled flight out as the test set: put it under to_label/test/
python3 prelabel.py --weights runs/detect/CURRENT_MODEL/weights/best.pt \
    --images flights/flight_TESTDATE/frames --out to_label/test/flight_TESTDATE --add-class OBJECT

# 6.4 correct every image in a labelling tool (YOLO format), following section 3

# 6.5 merge with ALL the data the current model was trained on
python3 merge_datasets.py --out datasets/merged_vN --overwrite \
    datasets/merged_vPREVIOUS to_label

# 6.6 check, then look at the preview images
python3 check_dataset.py datasets/merged_vN/data.yaml --imgsz 320 --preview 24

# 6.7 train from the current model, at the aircraft's input size
python3 train.py --data datasets/merged_vN/data.yaml \
    --model runs/detect/CURRENT_MODEL/weights/best.pt \
    --epochs 100 --imgsz 320 --device 0 --name OBJECT_vN

# 6.8 measure against section 5
yolo val model=runs/detect/OBJECT_vN/weights/best.pt \
    data=datasets/merged_vN/data.yaml split=test imgsz=320
python3 export.py --weights runs/detect/OBJECT_vN/weights/best.pt --imgsz 320 --model-name farm_vN
python3 ../tools/eval_model.py --model ../models/farm_vN.tflite \
    --labels ../models/farm_vN_labels.txt --format yolo \
    --images flights/flight_WITHOUT_OBJECT/frames --out check_vN --score 0.3

# 6.9 on the Pi: copy models/farm_vN.tflite + models/farm_vN_labels.txt,
#     edit config.yaml (section 8), then bench test
python3 run.py --config config.yaml --selftest
```

## 7. Results log

One row per trained model, including the ones you rejected.

| Version | Date | Train instances of OBJECT | OBJECT P / R / mAP50 (test flight) | Old classes mean mAP50 (val) | False alarms (flight without it) | Meets section 5? | Deployed? | Notes |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

## 8. Deployment

```yaml
# config.yaml on the aircraft
detector:
  model: models/farm_vN.tflite
  labels: models/farm_vN_labels.txt
  format: yolo
  tiled: false            # true if section 2 needs tiling
  tile_grid: [2, 2]
  class_thresholds:
    OBJECT: 0.45          # from section 1 (miss vs false alarm) and step 6.8
targets:
  - OBJECT
```

Previous model kept for rollback: `models/farm_vPREVIOUS.tflite` + labels.

## 9. Sign-off

- [ ] Section 2 verdict is GO (or the changes are made)
- [ ] Section 5 criteria were written down before training
- [ ] `check_dataset.py`: no errors; preview images look right
- [ ] Held-out test flight results meet section 5
- [ ] Old classes did not get worse than allowed
- [ ] Results log row filled in, including rejected models
- [ ] Bench test on the Pi passed; fps measured
- [ ] Previous model kept for rollback
- [ ] First flight with the new model reviewed (`tools/make_review.py`)
