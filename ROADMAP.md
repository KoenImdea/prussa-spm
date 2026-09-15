# Roadmap: Prusa SPM Scanner GUI

Convert the MVP scan loop from `notebooks/01-KL-MVP.ipynb` into a small standalone
app with a GUI, interactive scan-zone selector, and adjustable pixel density.

Target hardware: Prusa MK3S+ (PrusaLink G-code subset only: `G28`, `G90/G91`,
`G0/G1`, `M119`, `M400`, `M112` — all supported).

## 1. Architecture

```
3d_print_spm/
├── main.py              # entry point (thin: builds App, runs mainloop)
└── spm/
    ├── prusa.py         # PrusaScanner: serial comm, homing, probe step-down
    ├── scan.py          # ScanJob: grid generation, scan loop, cancellation, progress
    └── gui.py           # Tkinter app: bed map, zone selector, controls, live image
```

- **GUI: Tkinter + embedded Matplotlib canvas** (TkAgg). No new heavy dependencies
  (matplotlib, numpy, pyserial, spiepy are already in `pyproject.toml`).
- **Colormap**: all height maps use `spiepy.NANOMAP` (the NanoMap color scheme,
  imported from `spiepy`) instead of a Matplotlib default.
- **Threading model**: the scan runs in a **background thread** (serial I/O blocks
  for minutes). It communicates with the GUI only via a `queue.Queue` of events
  (`progress`, `point`, `done`, `error`). The GUI drains the queue with
  `root.after(100, ...)`. Keeps the UI responsive and makes aborting trivial.

## 2. `spm/prusa.py` — `PrusaScanner` (port of notebook logic)

- `connect(port, baud=115200)` with `dsrdtr=True`, boot wait, buffer reset
  (as in notebook cell 1).
- `send_command(cmd)` — existing ok-waiting helper, plus a **timeout** so a dead
  printer can't hang the thread forever.
- `is_pinda_triggered()` — M119 parse (existing logic).
- `probe_point(x, y, z_clearance, step=0.1, max_depth)` — travel → relative
  descent loop → return Z, or `None` on safety stop (plotted as NaN, not a bogus
  negative value like the current `Z_CLEARANCE - total_descended` when no contact).
- `probe_g30(x, y, z_clearance)` — firmware probe: single `G30` command, the
  firmware controls the descent and trigger detection (much faster than manual
  step-down). Parses `Bed X: … Y: … Z: …` from the response; Z < −2 → `None`
  (no contact).
- `home()`, `abort()` (raise Z fast; optional M112).

## 3. `spm/scan.py` — `ScanJob`

- Takes a **zone** (x0, y0, x1, y1) + **pixel pitch** (mm/px) → builds the grid
  with `np.arange` (allows non-integer pitches like 1.5 mm).
- `probe_mode`: `'g30'` (default, fast firmware probe) or `'manual'` (software
  step-down, more control over descent speed/step for delicate samples).
- Runs the loop, yields events, checks a `threading.Event` for abort between points.
- Optional **plate lift pause** (see hardware adaptations below).
- Writes results to `scans/scan_YYYYmmdd_HHMMSS.csv` (x, y, z) on completion.

## 4. GUI layout (single window)

```
┌────────────────────────────────────────────────┐
│  [Bed map: 250×210 mm top view]                │
│   drag a rectangle to select scan zone         │
│   (drawn with matplotlib, rubber-band select)  │
├────────────────────────────────────────────────┤
│  Zone:  X 58→103  Y 58→103   [Apply]          │
│  Pitch:  [3.0] mm/px   → 16×16 = 256 pts      │
│  Z clear: [8]  Step: [0.1]  Max depth: [10]   │
│  Probe: [g30 ▾]  (g30 = fast firmware probe)  │
│  Plate lift Z: [50] mm (0 = off)              │
│  Port: [/dev/cu.usbmodem21201]  [Connect]     │
│  [Start Scan] [Abort] [Continue] [Save PNG]   │
│  [Clear map] [Reset Zoom]                     │
│  Progress: 45/256  ETA 120s                   │
├────────────────────────────────────────────────┤
│  [Live image: imshow updating per row]         │
└────────────────────────────────────────────────┘
```

- **Zone selector**: click-drag on the bed map canvas → rectangle → updates zone
  fields. Bed limits enforced (0–250 × 0–210, small safety margin).
- **Pixel density**: pitch in mm/px; label live-updates the point count. During a
  scan the progress bar shows `done/total` plus a live **ETA** (measured s/point).
- **Live image**: `imshow` of the Z matrix (NANOMAP colormap), filled in row by
  row as the scan progresses (points arrive row-major). NaN cells shown
  transparent. Colorbar in mm.
- After scan: colorbar autoscale; PNG save button (CSV is saved automatically).
- **Zoom**: scroll wheel zooms the bed map at the cursor; **Reset Zoom** restores
  the full-bed view.
- **Scan archive / bed-map overlay**: every finished scan is drawn onto the bed
  map (NANOMAP, shared autoscaled colorbar) so multiple zones/scans can be
  compared at a glance. **Clear map** removes the overlay.
- **Prefill (coarse → fine workflow)**: when a new scan starts, NaN cells of the
  new grid are pre-filled with the nearest values from previously completed scans
  (newest first), so a fine scan immediately shows the underlying coarse data
  while it fills in.

## 5. Safety & robustness

- Abort button → sets event → thread finishes current point, raises Z to
  clearance, closes cleanly.
- Bed-boundary validation before start; "bed clear?" confirmation dialog before homing.
- No-contact points → NaN (excluded from colorbar range, shown as holes).
- Serial errors → error event → status bar + error dialog; the `homed` flag is
  reset (position unknown after a failure, so the next scan re-homes). The serial
  connection itself stays open.
- **Homing is skipped if already homed**: the GUI keeps a `homed` flag (set after
  a successful home, reset on disconnect/error), so repeated scans in one session
  don't re-home.

## 6. Implementation steps

- [x] 1. `spm/prusa.py` — extract serial logic from notebook. (Verified: grid
      math, connection failure handling. Hardware test pending.)
- [x] 2. `spm/scan.py` — grid + job thread. (Verified: 16×16=256 for the MVP
      zone; non-integer pitches work.)
- [x] 3. `spm/gui.py` — bed map + zone drag, controls, live plot. (Verified:
      simulated scan events render live image, NaN holes, progress, autoscale.)
- [x] 4. `main.py` — wire it up; update `pyproject.toml` script entry.
- [x] 5. Hardware tests with printer connected (2026-09-11, empty bed):
      - connect + M119 parse OK (firmware uses `open`/`TRIGGERED`, no `z_probe` line)
      - `G28 W` home OK (plain `G28` crashes firmware — never used)
      - single-point probe OK (Z≈2.3 mm on 3 mm test plate; bed is ~1 mm below
        zero at that spot due to warp, confirming the plate workaround is needed)
      - repeatability within a session: 0.1 mm (step resolution)
      - full `ScanJob` 3×3 grid → queue events → CSV OK
      - known artifact: perforated PCB plate → probe can poke through a hole
        and read the surface below (one point read 0.7 mm instead of ~2.3 mm).
        Use a solid plate for clean images.
- [x] 6. Live GUI run: zone drag, plate-lift pause/continue, live image.
- [x] 7. G30 firmware probe mode (`probe_g30`) as default; manual step-down kept
      as an option in the GUI ("Probe" combobox).
- [x] 8. Scan archive: finished scans drawn on the bed map with shared colorbar;
      Clear map button; prefill of new scans from previous (coarse → fine).
- [x] 9. NANOMAP colormap (spiepy) for all height maps.


## Notes / deviations

- `is_pinda_triggered()` now excludes `NOT_TRIGGERED` (the notebook's
  `"triggered" in line` would also match `NOT_TRIGGERED`).
- No-contact points return `None` → stored as empty in CSV and NaN in the
  image (instead of a bogus `Z_CLEARANCE - total_descended` value).
- `max_depth` is validated to be ≤ Z clearance so the bed can never be hit.
- Old `src/3d_print_spm/` package (invalid module name — starts with a digit)
  deleted 2026-09-14.

## Hardware-specific adaptations (added after first review)

- **Sensor offset**: the probe is not directly under the nozzle → scannable X
  restricted to **0–210 mm** (`SCAN_X_MAX`). Bed map shows the 210–250 zone
  hatched as "no scan"; zone selection and validation are clamped accordingly.
- **Homing**: only `G28 W` is used (plain home). Plain `G28` runs auto bed
  level and crashes the old firmware — never used.
- **Warped bed workaround**: configurable **Plate lift Z** (default 50 mm, 0 =
  off). After `G28 W` the head lifts to that height and the scan **pauses**
  until the user places a flat ~3 mm plate and clicks **Continue**. Abort also
  works while paused. Verified with a fake scanner (pause → continue → done,
  pause → abort → aborted).
- **G30 probing** (default): the firmware does the descent, so trigger detection
  happens in the firmware's real-time loop — faster and finer than the manual
  M119-polling loop. No-contact is reported as Z < −2 and mapped to NaN.

## 7. Documentation & UX Improvements

- [x] Add numpy-style docstrings to all core classes and methods for better maintainability.
- [x] Create comprehensive documentation (`README.md`, `docs/setup.md`, `docs/usage.md`, `docs/theory.md`).
- [x] Implement zoom functionality for the bed map (scroll to zoom at cursor, Reset Zoom button).
- [x] Sync docs with code: G30 probe mode, scan archive/overlay, coarse→fine prefill,
      homing-skip flag, NANOMAP colormap (2026-09-14).
