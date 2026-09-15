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

## Installation

### Hardware Requirements
- Prusa MK3S+ printer.
- A cantilever assembly that triggers the PINDA sensor upon contact.
- USB connection between the printer and the computer.

### Software Requirements
- Python 3.14+
- Dependencies: `pyserial`, `numpy`, `matplotlib`, `spiepy`

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

## File Structure
- `main.py`: Entry point.
- `src/spm/prusa.py`: Serial communication and G-code implementation.
- `src/spm/scan.py`: Scan grid generation and worker thread logic.
- `src/spm/gui.py`: Tkinter user interface and visualization.
- `docs/`: Setup, usage, and theory documentation.
- `scans/`: Directory containing the resulting CSV data.
