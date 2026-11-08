# Third-party notices and credits

The UAV SILM team's own files are under the MIT licence (`LICENSE`). This
page lists everything in this folder that was made by others, and the
software and data the programs use, with their licences. Thank you to all of
them.

## Included in this folder

| What | Where | Made by | Licence |
|---|---|---|---|
| **Leaflet 1.9.4** (the map in the Ground Station) | `groundstation/static/vendor/leaflet/` | Volodymyr Agafonkin, CloudMade and contributors | BSD-2-Clause, text in `vendor/leaflet/LICENSE` |
| **ArduPilot firmware uploader** (`uploader.py`, used for "Update ArduPlane") | `groundstation/vendor/ardupilot_uploader.py` | PX4 Development Team; from the ArduPilot project | BSD-3-Clause, notice at the top of the file |
| **Bricolage Grotesque** font | `groundstation/static/fonts/bricolage-*.woff2` | The Bricolage Grotesque Project Authors (Mathieu Triay) | SIL Open Font License 1.1, text in `fonts/OFL-BricolageGrotesque.txt` |
| **IBM Plex Mono** font | `groundstation/static/fonts/plex-mono-*.woff2` | IBM Corp. | SIL Open Font License 1.1, text in `fonts/OFL-IBMPlexMono.txt` |
| **Ready-made model `silm_oi`** (tractor, car, truck, livestock) | `models/silm_oi.tflite`, `_labels.txt`, `.json` | UAV SILM team, fine-tuned from **Ultralytics YOLO11n** (© Ultralytics) | **AGPL-3.0** (inherited from YOLO11n), text in `models/LICENSE-silm_oi-AGPL-3.0.txt`. Its source: `training/` (`openimages_dataset.py`, `train.py`, `export.py`) and the steps in `training/README.md` |
| Training photos and boxes behind `silm_oi` (not included, downloaded by `training/openimages_dataset.py`) | - | **Google Open Images V6/V7**; photos by their Flickr authors | Boxes and labels CC BY 4.0 (Google); photos CC BY 2.0 (each author) |
| Four test photos | `tests/data/oi_samples/*.jpg` | Flickr authors D. Miller, Jason Lawrence, Frank Deanrdo and spotter_nl, via Open Images; author, title and source of each in `tests/data/oi_samples/samples.json` | CC BY 2.0 (https://creativecommons.org/licenses/by/2.0/); resized to 640 px |
| COCO class names | `models/coco_labels.txt` | COCO Consortium (dataset), TensorFlow authors (model label map) | COCO annotations CC BY 4.0; the standard model is Apache-2.0 and is **downloaded** by `pi/install.sh`, not included |

## Used but not included (installed separately)

| Software | Used for | Licence |
|---|---|---|
| **ArduPilot / ArduPlane** | the flight controller firmware and the simulator (SITL). `ardupilot/gps_denied_failsafe.param` and `ardupilot/scripts/silm_gps_loss.lua` are our settings and script *for* it | GPL-3.0 |
| COCO SSD MobileNet v1 (quantised), TensorFlow | the standard recognition model, downloaded by `pi/install.sh` | Apache-2.0 |
| pymavlink | talking to the flight controller | LGPL-3.0 |
| MAVProxy (optional) | simulator console | GPL-3.0 |
| LiteRT (`ai-edge-litert`), `litert-torch`, `ai-edge-quantizer` | running and exporting models | Apache-2.0 |
| OpenCV | camera frames and pictures | Apache-2.0 |
| NumPy | maths | BSD-3-Clause |
| PyYAML | settings files | MIT |
| pySerial | finding USB ports | BSD-3-Clause |
| Paramiko (with cryptography, bcrypt, PyNaCl) | installing the aircraft computer over SSH from the Ground Station | LGPL-2.1 (Paramiko; source at github.com/paramiko/paramiko), Apache-2.0 / BSD (the others) |
| Matplotlib | simulator result picture | Matplotlib licence (PSF-based) |
| Ultralytics, PyTorch | training your own models (optional) | AGPL-3.0, BSD-3-Clause |
| pytest, lupa, Playwright | automated tests | MIT, MIT, Apache-2.0 |

## Inside the desktop app (the installers)

The Ground Station installers for Windows, macOS and Linux are built with
PyInstaller and contain, unchanged: **Python** (PSF License), **PyYAML** (MIT),
**pymavlink** (LGPL-3.0), **pySerial** (BSD-3-Clause), **pywebview**
(BSD-3-Clause), **Paramiko** (LGPL-2.1) with **cryptography**, **bcrypt** and
**PyNaCl** (Apache-2.0 / BSD), and PyInstaller's start-up program (GPL-2.0
with an exception that allows this). The LGPL libraries are used as they are;
their source code is on pypi.org and their projects' pages, and you may
replace them by running the Ground Station from the source folder
(`python -m groundstation`) with your own copies.

## Online services the Ground Station shows

- **Map: © OpenStreetMap contributors**, data under the Open Database
  License (ODbL), tiles from tile.openstreetmap.org under the OSMF tile usage
  policy. Don't pre-download (bulk-cache) these tiles.
- **Satellite imagery: © Esri and its data providers** (World Imagery),
  under Esri's terms of use.
- Both credits are shown on the map itself.

## Documentation we relied on

ArduPilot Plane documentation, SIYI A2 mini user manual (camera address and
gimbal commands), Matek H743-WING manual, Raspberry Pi documentation, Open
Images and Ultralytics documentation. Parts of the software and documentation
were written with the help of Claude (Anthropic).
