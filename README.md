# Prusa SPM Scanner

This project converts a Prusa MK3S+ 3D printer into a basic Scanning Probe Microscope (SPM) demo. It uses the printer's precision motion system and the PINDA inductive sensor to map the surface topography of an object.

## Overview

The system works by attaching a cantilever (probe) to the print head. When the cantilever touches the surface of the sample, it triggers the PINDA sensor. By scanning the probe across a grid of X and Y coordinates and recording the Z-height at the moment of contact, a 3D height map of the surface is generated.

### Key Features
- **Interactive GUI**: Visually select the scan area on the bed map (drag a rectangle, or type coordinates). Scroll wheel zooms the map; **Reset Zoom** restores the full bed.
- **Two Probing Modes**:
    - `g30` (default): Uses the printer's built-in G30 probe command — the firmware controls the descent and trigger detection, which is fast.
    - `manual`: A software-controlled step-down approach (configurable step size) for more control on delicate samples.
- **Live Visualization**: Real-time rendering of the surface height map (NANOMAP colormap from `spiepy`) with a live progress bar and ETA.
- **Scan Archive**: Every finished scan is drawn onto the bed map with a shared colorbar, so multiple zones/scans can be compared at a glance (**Clear map** removes the overlay).
- **Coarse → Fine Prefill**: When a new scan starts, its empty cells are pre-filled from previously completed scans, so a fine scan shows the underlying coarse data immediately.
- **Plate Lift Workaround**: Lifts the Z-axis after homing so you can place a flat reference plate — mitigates the effects of a warped bed.
- **Data Export**: Scans are automatically saved as CSV files in `scans/` and can be exported as PNG images.
- **Demo Mode** (`--demo`): A presentation mode that looks exactly like a normal scan, but the tip flies a fast hover trajectory over the *known* demo surface (no probing, no contact) while the height map is populated from the surface model — a full 100-point scan finishes in ~30 s instead of an hour. See [Demo Mode](#demo-mode).

## Installation

### Hardware Requirements
- Prusa MK3S+ printer.
- A cantilever assembly that triggers the PINDA sensor upon contact.
- USB connection between the printer and the computer.

### Software Requirements
- Python 3.14+
- Dependencies: `pyserial`, `numpy`, `matplotlib`, `spiepy`, `scipy`, `scikit-image`

The project is managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync          # creates .venv and installs all dependencies
uv run main.py   # or: uv run 3d-print-spm
```

Without uv:

```bash
pip install pyserial numpy matplotlib spiepy
python main.py
```

## Usage

1. **Connect**: Enter the serial port of your Prusa printer and click **Connect**.
2. **Select Zone**: Drag on the bed map to select the area to be scanned, or enter the coordinates manually. (Scannable X is limited to 0–210 mm because of the PINDA sensor offset.)
3. **Configure**:
   - Set the **Pitch** (distance between points).
   - Choose the **Probe Mode** (`g30` or `manual`).
   - Set **Z clearance**, **Step**, and **Max depth**.
   - Optionally set **Plate lift Z** for the warped-bed workaround.
4. **Start Scan**: Click **Start Scan**.
   - If the printer isn't homed yet, it will perform a `G28 W` (Z-homing only, no bed leveling). Repeated scans in one session skip homing.
   - If **Plate lift Z** is set, the printer will lift and wait for you to place a reference plate. Click **Continue** once ready.
5. **Monitor**: Watch the live height map, progress bar, and ETA.
6. **Save**: Once complete, the data is saved to a CSV file in the `scans/` directory. You can also save the final map as a PNG.

See `docs/` for more: [`setup.md`](docs/setup.md) (hardware + software setup), [`usage.md`](docs/usage.md) (detailed scan workflow), [`theory.md`](docs/theory.md) (how it works).

## Demo Mode

For presentations (e.g. to students), launch the GUI with `--demo`:

```bash
uv run 3d-print-spm --demo      # or: uv run python main.py --demo
```

The GUI is **intentionally identical** to a normal session — no button, label, or dialog reveals that it is a demo. The difference is entirely in the scan job (`ScanConfig.demo`):

- **No probing.** The tip flies the scan grid at maximum speed, hovering ~1.5 mm above the surface. Instead of lifting to full Z clearance between points, each segment flies at a *corridor height* — the max surface height along that segment plus margin — so the tip only moves as much as the terrain requires (this also clears the plate edge wall).
- **Data from the known surface.** The z values shown in the live map and saved to CSV come from the demo-surface model, not the PINDA sensor. Events, status messages, progress bar, and CSV output are byte-for-byte the same as a real scan.

### Demo surface model

The model predicts the absolute bed-frame height at any point (`src/spm/demo.py`, `SurfaceModel`):

- **On the plate**: `plane(x, y) + heightmap_sample(x, y)`. The heightmap is the printed demo surface design (`demo_surface/heightmap_010.npy`, flattened with `spiepy`), positioned by the estimated plate pose. The plane is fitted so the model matches the *measured* coarse-scan z values on the plate — it absorbs glue thickness and bed warp.
- **Off the plate**: bilinear interpolation of the measured coarse-scan bed heights (the bed is warped by ~2 mm corner to corner, so this matters).

**Safety:** physical hover heights get an extra 2 mm safety offset on top of the model (the model under-predicts in places; the plane calibration is a known TODO). The reported *data* does not include this offset.

### Rehearsing without the GUI

`scripts/demo_scan.py` drives the same trajectory from the command line:

```bash
uv run python scripts/demo_scan.py --dry-run     # print the G-code sequence + time estimate
uv run python scripts/demo_scan.py --test-point  # single hover at the plate center
uv run python scripts/demo_scan.py               # fly the full 10x10 serpentine
```

Recommended order before a live demo: `--dry-run`, then `--test-point` (watch the hover gap), then the full run. Ctrl-C lifts to a safe Z and disconnects.

## Plate Position Estimation

The demo surface is glued to the bed at an arbitrary position. `scripts/estimate_position.py` estimates its pose (center + rotation) from the scan data:

1. **Coarse stage** (10 mm pitch scan over 0–210 mm): segments plate vs. bed (Otsu threshold), fits the visible left and top edge lines → rough plate center and rotation.
2. **Fine stage** (2 mm pitch scan): flattens both the scan and the heightmap window (`spiepy` plane removal), searches rotation in [−3, +3]°, and for each angle directly cross-correlates (`scipy.signal.correlate2d`, windowed ±15 mm with parabolic sub-pixel refinement) the sampled heightmap with the scan → sub-mm center and rotation.

```bash
uv run python scripts/estimate_position.py
```

prints the pose and saves an overlay figure (`scripts/position_estimate.png`). The resulting pose is hardcoded as constants in `src/spm/demo.py` (`PLATE_CX`, `PLATE_CY`, `PLATE_THETA_DEG`) — update them there if the plate is repositioned.

## File Structure
- `main.py`: Entry point (`--demo` flag).
- `src/spm/prusa.py`: Serial communication and G-code implementation.
- `src/spm/scan.py`: Scan grid generation and worker thread logic (incl. the demo-mode flight in `ScanJob._run_demo`).
- `src/spm/demo.py`: Demo-surface model and fast hover trajectory (shared by the GUI demo mode and the CLI script).
- `src/spm/gui.py`: Tkinter user interface and visualization.
- `scripts/estimate_position.py`: Estimate the demo surface pose (center + rotation) on the bed.
- `scripts/demo_scan.py`: CLI driver for the demo hover trajectory (dry-run / test-point / live).
- `demo_surface/`: Demo surface design (STEP/STL) and its heightmap (`heightmap_010.npy`, 2000×2000 px over 200×200 mm).
- `docs/`: Setup, usage, and theory documentation.
- `scans/`: Directory containing the resulting CSV data.
