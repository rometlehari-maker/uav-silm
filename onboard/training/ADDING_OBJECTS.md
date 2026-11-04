# Adding a new object and training the detector on it

The detector only recognises the classes it was trained on. To teach it
something new (say **deer**, so you can find fawns in a meadow before it is
mown) you give it a name, show it a few hundred labelled examples, retrain from
the current model, check that it learnt, and put the new model on the aircraft.

This guide walks through that end to end. Every command below was run while
writing it; the outputs shown are real.

| Step | What you do | Tool |
|---|---|---|
| 0 | Practise the whole loop on synthetic data (10 minutes, no photos needed) | all of the below |
| 1 | Check the object is big enough to see from your altitude | `tools/pixels_on_target.py` |
| 2 | Add the class name | `training/classes.yaml` |
| 3 | Collect pictures | your flights (`save_all_frames`), public datasets |
| 4 | Label them (with the current model drafting labels for you) | `training/prelabel.py` + a labelling tool |
| 5 | Merge new and old data into one dataset | `training/merge_datasets.py` |
| 6 | Check the dataset and look at the labels | `training/check_dataset.py` |
| 7 | Train, starting from your current model | `training/train.py` |
| 8 | Measure whether it learnt, and whether old classes got worse | Ultralytics output, `tools/eval_model.py` |
| 9 | Export and put it on the aircraft | `training/export.py`, `config.yaml` |
| 10 | Fly, collect its mistakes, repeat | `prelabel.py --select uncertain` |

All commands run from `onboard/training/` on your PC unless noted. You need
`pip install ultralytics litert_torch` there (see `training/README.md`). A GPU
makes step 7 10-100x faster but is not required.

---

## 0. Practise first, without any real photos

Do this once. It runs every step on synthetic images, so you learn the
workflow and see a new class appear before you spend hours labelling. Use a
scratch copy of the class list so your real one stays untouched:

```bash
cd onboard/training
mkdir -p practice
cp classes.yaml practice/classes.yaml
echo "  15: deer" >> practice/classes.yaml

# "photos of the new object, labelled in a labelling tool": a folder of images
# with a .txt beside each and a classes.txt that only knows 'deer' (index 0)
python3 make_synthetic.py --classes practice/classes.yaml --only deer \
    --layout flat --n-train 160 --width 416 --height 416 --out practice/deer_labelled

# the existing data (15 classes)
python3 make_synthetic.py --out datasets/synth --n-train 350 --n-val 70 --width 416 --height 416

# merge: 'deer' is index 0 in the new folder and becomes 15 in the merged set
python3 merge_datasets.py --classes practice/classes.yaml --out practice/merged \
    datasets/synth practice/deer_labelled
python3 check_dataset.py practice/merged/data.yaml --imgsz 256 --preview 8

# train from the existing model (if you don't have one yet: --model yolo11n.pt)
python3 train.py --data practice/merged/data.yaml \
    --model runs/detect/synth_proof/weights/best.pt \
    --epochs 15 --imgsz 256 --device cpu --name practice_deer

python3 export.py --weights runs/detect/practice_deer/weights/best.pt \
    --imgsz 256 --model-name practice_deer
python3 ../tools/eval_model.py --model ../models/practice_deer.tflite \
    --labels ../models/practice_deer_labels.txt --format yolo \
    --images practice/merged/images/val --out practice/annotated
```

What happened when this guide was written:

* **Merge:** `deer` was class 0 in the labelling-tool folder and became class
  15 in the merged dataset (`deer_labelled__img_00000.txt` starts with `15`):
  ```
  source synth: 420 images, classes ['person', 'car', ..., 'fire', 'smoke']
  source deer_labelled: 160 images, classes ['deer']
  merged 580 images: train 494, val 86
    deer                 354     48      0
  ```
* **Check:** no errors, "OK to train". The preview caught a real bug (step 6).
* **Train:** 15 epochs took 4.5 minutes on a 4-core CPU, no GPU. Ultralytics
  printed `Overriding model.yaml nc=15 with nc=16` and `Transferred 499/499
  items` (explained in step 7). Final validation:
  ```
                 Class     Images  Instances      Box(P          R      mAP50  mAP50-95)
                   all         86        220      0.933      0.891      0.966      0.757
                  deer         16         48      0.748      0.938       0.92       0.71
  ```
  The new class reached mAP50 0.92 and the old ones stayed at ~0.97.
* **Export:** printed `NOTE: this model's classes differ from
  training/classes.yaml` (expected here: the practice used a copy of the class
  list) and wrote a 16-name labels file from the model.
* **Onboard check:** through the aircraft's own detector code, deer were found
  in all 16 deer validation images (43 detections for 48 labelled deer), with
  no other class reported.

Synthetic shapes are much easier than real deer in grass: expect real numbers
to be lower and to need far more data. The point of the practice run is that
every step works and you know what each output means.

Open `practice/merged/preview/` and `practice/annotated/` to see the labels and
the new model's detections. Then delete `practice/` and do it for real.

---

## 1. Can the camera actually see it?

A detector needs the object to be roughly **10 pixels or more across at the
model's input**, and under ~5 it can't learn it at all. The camera frame is
shrunk to the model input (1280 → 320 px on the Pi 3), so small objects shrink
with it. Check before collecting anything:

```bash
cd onboard
python3 tools/pixels_on_target.py --object-m 0.5 --length-m 1.5 --alt-m 100 --config config.yaml
```

Pass the object's **smaller side as seen from above** (`--object-m`) and
optionally its length (`--length-m`). Computed for this airframe's camera
(86° lens, 1280-px frame) at the model's input:

| Object (length × width from above) | 100 m, 320 px | 60 m, 320 px, 2×2 tiles |
|---|---|---|
| Combine without header, ~10 × 3.5 m | 17 × 6 px, marginal | 57 × 20 px, good |
| Car, ~4.5 × 1.8 m | 7.7 × 3.1 px, too small | 26 × 10 px, good |
| Cow, ~2.4 × 0.8 m | 4.1 × 1.4 px, too small | 14 × 4.6 px, too small |
| Deer, ~1.5 × 0.5 m | 2.6 × 0.9 px, too small | 8.6 × 2.9 px, too small |

So at 100 m on a Pi 3 without tiling, only large objects (combines with
headers, buildings, fire and smoke plumes, timber stacks) are realistic. The
tool also tells you what would fix it, e.g. for a deer: fly at or below ~26 m,
~51 m with 2×2 tiled detection (`detector.tiled: true`), or ~100 m with a
640-px model and tiles. Tiling and bigger inputs need far more computing; on a
Pi 3 that realistically means adding a Coral USB accelerator. Decide this now:
the altitude you will fly at is the altitude your training pictures must show.

Also decide what exactly the class is:

* **Name**: lowercase with underscores (`irrigation_pivot`, not `Irrigation Pivot`).
* **Never rename a class later.** The name is what carries its training from one
  model to the next (see step 7). Renaming is a new class starting from zero.
* **One class per thing you need to tell apart.** If two objects look the same
  from 100 m and you don't need to separate them, make them one class.
  "combine" and "harvester" only work as separate classes if a labeller can
  tell them apart in your photos every time.
* **Write a one-line labelling rule** ("deer: any deer or fawn, standing or
  lying, label the whole body, not the shadow") so everyone labels the same way.

## 2. Add the class name

Append it to `training/classes.yaml`, **at the end**, with the next number:

```yaml
  13: fire
  14: smoke
  15: deer              # NEW: any deer or fawn, whole body, not the shadow
```

Never insert it in the middle or renumber existing classes: old label files
store numbers, and the deployed labels file maps numbers to names.

## 3. Collect pictures

In order of how much they help:

1. **Your own flights.** Set `storage.save_all_frames: true` (one frame every
   `frame_interval_s` seconds) and fly over places where the object is. These
   match your camera, altitude, angle and fields exactly, which nothing else
   does. After landing, copy each flight's `frames/` folder into its own folder,
   e.g. `new_photos/flight_1110/`. **Keep one subfolder per flight**: the merge
   step uses it to keep each flight's near-identical frames together.
2. **Public aerial datasets** (Roboflow Universe, Kaggle, research datasets).
   Search for drone/aerial images of the object. Check the licence, and check
   the altitude: drone photos from 10 m don't teach a 100 m detector much.
3. **Ground-level photos.** Mostly useless here: a deer from the side looks
   nothing like a deer from 100 m above.

**How much:** a first useful version from ~200-300 labelled objects; a reliable
one usually needs over a thousand, in varied conditions (seasons, light,
crops, standing/lying, partly hidden). Also include **look-alike photos without
the object** (brown bushes, hay piles, shadows for deer): images with no boxes
teach the model what *not* to report.

## 4. Label

### 4a. Let the current model draft the labels

```bash
python3 prelabel.py --weights runs/detect/farm_v1/weights/best.pt \
    --images new_photos/flight_1110 --out to_label/flight_1110 --add-class deer
```

This copies the photos into `to_label/flight_1110/`, writes a `.txt` with the
model's guesses beside each one, and a `classes.txt` listing the model's
classes plus `deer` at the end. The model doesn't know deer yet, so you draw
those; but every tractor, car and hay bale it already finds is pre-drawn. Use
`--select uncertain --max 300` to keep only the frames the model was least sure
about: those teach it most per minute of labelling.

### 4b. Correct them in a labelling tool

Any tool that reads and writes **YOLO-format** labels (one `.txt` per image,
lines of `class x_centre y_centre width height`, all 0-1) works. Options:

* **Roboflow** (web, free tier): upload the folder including the `.txt` files
  and `classes.txt`; it imports the drafts. Export again in YOLO format.
* **CVAT** (web or self-hosted, free): create a task with the class names from
  `classes.txt`, import the drafts as YOLO annotations, export as YOLO.
* **Label Studio**, **X-AnyLabeling** and similar desktop tools also work.

Whatever you use, the result must be a folder of images plus YOLO `.txt` files
and the class list (a `classes.txt` or a `data.yaml`). The merge step reads the
class *names* from it, so it doesn't matter what numbers the tool used.

### 4c. Labelling rules that decide whether it works

* **Label every instance in every image.** An unlabelled deer in a labelled
  image teaches the model "this is not a deer". This is the most common mistake.
* **Tight boxes around the whole visible object.** Not the shadow, no big
  margins, nothing cut off.
* **Partly visible at the edge or behind a tree:** label it if you can tell
  what it is (a sensible rule: more than about a third visible). Be consistent.
* **Too small or blurry to identify:** leave it out, consistently.
* **Delete wrong drafts** rather than leaving them: a false draft box is a
  wrong label.
* When several people label, share the one-line rules from step 1.

## 5. Merge new and old data

Always train on **old and new data together**. A model trained only on deer
photos forgets the other classes (see step 7 for measured numbers).

```bash
# sources: what the current model was trained on, your corrected new labels,
# and a public dataset if you used one
python3 merge_datasets.py --out datasets/merged \
    datasets/farm_v1 to_label/flight_1110 downloads/roboflow_deer
```

It reads each source's own class names, maps them onto `classes.yaml` by name
(case, spaces and hyphens don't matter) and rewrites every label with the
right number. If a source uses a name you don't have, it stops and lists it.
You then either add the class, map it (`--alias "Roe Deer=deer"`), or drop
those boxes (`--drop-unknown`).

Images already in `train`/`val`/`test` folders keep that split. Your own
flights have no split, so whole subfolders (flights) go to validation by
`--val-frac` (default 15%). The assignment is fixed, so re-running the merge
after adding a flight doesn't reshuffle the others. Re-run with
`--overwrite` to rebuild the merged set.

It prints how many boxes each class has in train and val, and saves that in
`merge_report.txt`.

## 6. Check the dataset, and look at it

```bash
python3 check_dataset.py datasets/merged/data.yaml --imgsz 320 --preview 24
```

Fix every **ERROR** (bad lines, wrong class numbers, boxes outside the image)
before training. Read the **WARNINGS**:

* *too small to learn at imgsz 320*: those boxes are below ~3 px at the size you
  train at. Go back to step 1.
* *same image in train/val*: leakage; the validation score will be too good.
* *no validation boxes*: you can't measure that class. Add a flight containing it.
* *only N training boxes*: expect weak results until you add more.

Then **open `datasets/merged/preview/`** and look at the pictures. The preview
picks images with your rarest classes first. When this guide was written, the
preview caught a real bug in the synthetic generator: boxes were drawn from
each shape's unrotated size, so rotated objects stuck out of their boxes. Badly
fitting boxes are a common reason a class doesn't learn, and you only see them
by looking.

## 7. Train, starting from your current model

```bash
# on a GPU PC
python3 train.py --data datasets/merged/data.yaml \
    --model runs/detect/farm_v1/weights/best.pt \
    --epochs 100 --imgsz 320 --batch 32 --device 0 --name farm_v2
```

* **`--model` = your current best model**, not a fresh one: it keeps everything
  already learnt. Train from `yolo11n.pt` only for your very first model.
* **`--imgsz` = the size the aircraft runs at** (320 on a Pi 3; 640 with a
  Coral). Training at one size and running at another costs accuracy.
* **Epochs:** 50-150 for real data. `train.py` stops early when validation stops
  improving (`--patience 30`).
* On a CPU, use `--device cpu --batch 8` and expect hours. Interrupted? Re-run
  with `--resume`.

At the start, Ultralytics prints lines like these (from this guide's run):

```
Overriding model.yaml nc=15 with nc=16
Transferred 499/499 items from pretrained weights
```

`nc` is the number of classes: the model is being widened from 15 to 16.
"Transferred 499/499" means every learnt weight was kept: the version used here
(Ultralytics 8.4) matches the classification layer to the class **names**, so
the 15 known classes keep what they knew and only "deer" starts from scratch.
That is why names must never change. Older Ultralytics versions restart that
last layer for every class; the backbone still carries over, training just
takes a bit longer.

**Why the old data must be included**, measured:

Starting from the same 15-class model and training the same 15 epochs,
once on old + new data and once on the deer photos alone, then validating both
on the full validation set (mAP50 per class):

| Class | Old + new data | Deer photos only |
|---|---|---|
| tractor | 0.995 | 0.000 |
| combine | 0.934 | 0.000 |
| fire | 0.995 | 0.000 |
| car | 0.995 | 0.415 |
| person | 0.953 | 0.142 |
| **old 15 classes, mean** | **0.969** | **0.066** |
| **deer (the new class)** | **0.920** | **0.383** |

Trained on deer alone, the model forgot almost everything else, and it was
worse at deer too, because it never saw anything that wasn't a deer. Always
merge the old data back in.

## 8. Did it learn? Did anything get worse?

When training ends, Ultralytics prints a table for the validation set:

```
                 Class     Images  Instances      Box(P          R      mAP50  mAP50-95)
                   all ...
                  deer ...
```

* **P (precision):** of the boxes it drew, how many were right. Low = false alarms.
* **R (recall):** of the real objects, how many it found. Low = misses.
* **mAP50:** overall detection quality per class, 0-1. Compare it **with the
  previous model's numbers for the old classes**: if they dropped noticeably,
  the new data is crowding them out (add more old-class images or train longer).

In `runs/detect/farm_v2/` also open:

* `confusion_matrix.png`: what gets mistaken for what. "deer" predicted as
  "livestock" means those two need clearer examples (or should be one class).
* `val_batch0_labels.jpg` vs `val_batch0_pred.jpg`: your labels next to the
  model's predictions on the same images.
* `results.png`: training curves. Validation loss rising while training loss
  falls means overfitting: more data, fewer epochs.

The validation set comes from the same sources as the training data. The
honest test is **a flight the model has never seen**:

```bash
cd ..   # onboard/
python3 tools/eval_model.py --model models/farm_v2.tflite \
    --labels models/farm_v2_labels.txt --format yolo \
    --images /path/to/a_new_flight/frames --out /tmp/check_v2 --score 0.3
```

Look through `/tmp/check_v2/`. Note the score of right and wrong detections
for the new class: that tells you its threshold for step 9.

## 9. Export and put it on the aircraft

```bash
python3 export.py --weights runs/detect/farm_v2/weights/best.pt \
    --imgsz 320 --model-name farm_v2          # add --edgetpu for a Coral
```

This writes `models/farm_v2.tflite` and `models/farm_v2_labels.txt`. The labels
come from the model itself, so they always match it, even if `classes.yaml`
has changed since. Keep the previous model's files: use a new name per version
so you can switch back by editing one line.

Copy both files to the Pi (`onboard/models/`) and edit `config.yaml`:

```yaml
detector:
  model: models/farm_v2.tflite
  labels: models/farm_v2_labels.txt
  format: yolo
  class_thresholds:      # optional, from what you saw in step 8
    deer: 0.35           # lower: rather a false alarm than a missed fawn
targets:
  - deer                 # add it, or it is detected but never saved
  - tractor
  # ...
```

If the model and labels file don't match, the app prints
`[detector] WARNING: model has N classes but ... has M names` at start-up.

Bench test with photos containing the object, then fly:

```bash
python3 run.py --config config.yaml --selftest     # camera.source: file + a folder of photos
```

## 10. Fly, collect the mistakes, repeat

The first version will miss things and report a few false ones. Every loop
fixes some:

1. Fly with `save_all_frames: true` over places with and without the object.
2. Let the model pick the frames worth labelling:
   ```bash
   python3 prelabel.py --weights runs/detect/farm_v2/weights/best.pt \
       --images flights/flight_1118/frames --out to_label/flight_1118 \
       --select uncertain --max 300
   ```
3. Correct them (step 4), merge everything again (step 5, `--overwrite`),
   check (6), train from `farm_v2` (7), compare the numbers with `farm_v2` (8).
4. Deploy only if it is better on the flight it has never seen.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| New class never detected | Too small at the model input; too few labels; labelled differently each time | Step 1; add data; check the preview |
| Old classes got worse | Trained mostly or only on new data | Merge old data back in (step 5) |
| Many false alarms of the new class | No look-alike images without it | Add background images; raise its `class_thresholds` |
| Good validation score, poor in flight | Train and val too similar (frames from one flight in both); different altitude/season | Keep flights in subfolders; collect from your real flights |
| `merge_datasets` stops listing names | A source uses a name not in `classes.yaml` | Add the class, `--alias`, or `--drop-unknown` |
| `class index 3 but the source only declares 1 classes` | The `.txt` files and `classes.txt` don't belong together | Re-export from the labelling tool |
| Boxes in the preview are offset or the wrong size | Labels in pixels or as corners instead of YOLO's normalised centre/size | Export as YOLO format |
| `[detector] WARNING: model has N classes...` on the Pi | Wrong labels file next to the model | Use the `_labels.txt` that `export.py` wrote with that model |
| Detected but never saved | Not in `targets`, below its threshold, or seen in only one frame | Add to `targets`; check `class_thresholds`; `confirm.frames` |

## Files used in this guide

| File | Purpose |
|---|---|
| `tools/pixels_on_target.py` | Is the object big enough at your altitude? |
| `training/classes.yaml` | The class list (append only) |
| `training/prelabel.py` | Draft labels with the current model; pick uncertain frames |
| `training/merge_datasets.py` | Combine datasets, remapping classes by name |
| `training/check_dataset.py` | Find label problems; preview boxes |
| `training/train.py` | Train or fine-tune |
| `training/export.py` | Export `.tflite` + matching labels |
| `tools/eval_model.py` | Run a model through the onboard code on any photos |
| `training/make_synthetic.py` | Practice data (step 0) |
| `config.yaml` | `detector.*`, `targets`, `class_thresholds` on the aircraft |
