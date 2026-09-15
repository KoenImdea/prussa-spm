# Usage Guide

Detailed instructions on how to perform a surface scan using the Prusa SPM Scanner.

## Step-by-Step Process

### 1. Connection
- Plug your Prusa MK3S+ into your computer via USB.
- In the GUI, enter the serial port (e.g., `/dev/cu.usbmodem...` on macOS, `/dev/ttyACM0` on Linux, `COMx` on Windows).
- Click **Connect**. The status bar should indicate a successful connection; the button changes to **Disconnect**.

### 2. Defining the Scan Area
There are two ways to define the area to scan:
- **Visual Selection**: Click and drag on the bed map. The red rectangle indicates the current scan zone.
- **Manual Entry**: Enter the $X_0, X_1, Y_0, Y_1$ values in the input fields (the rectangle follows).

*Note: The scan area is limited to $X \le 210\text{mm}$ due to the offset of the PINDA sensor relative to the nozzle. The 210–250 mm zone is shown hatched on the bed map.*

**Zoom**: scroll the mouse wheel over the bed map to zoom in/out around the cursor (useful for placing a small zone precisely). **Reset Zoom** restores the full-bed view.

### 3. Configuring Scan Parameters
- **Pitch**: The spacing between probe points. A smaller pitch results in higher resolution but longer scan times. The label next to it shows the resulting grid size (e.g., `16 x 16 = 256 points`).
- **Probe Mode**:
    - `g30` (default): Uses the printer's internal `G30` command. This is the fastest method as the firmware handles the descent and trigger detection.
    - `manual`: The software controls the descent in small steps (see **Step**). This is useful for very delicate samples or when more control over the Z-descent is needed.
- **Z Clearance**: The height the probe maintains while traveling between points. Ensure this is high enough to clear any protrusions on your sample.
- **Step** (manual mode): Z-step size during descent (e.g., 0.1 mm). Smaller = finer resolution, slower.
- **Max Depth**: The maximum distance the probe will descend before giving up (validated to be ≤ Z clearance, so the bed can never be hit).

### 4. Handling Bed Warp (Plate Lift)
If your bed is warped, the absolute Z-values will be skewed. To fix this:
1. Set a **Plate lift Z** value (e.g., $50\text{mm}$).
2. When you start the scan, the printer will home and then lift to this height and **pause**.
3. Place a perfectly flat, **solid** reference plate on the bed.
4. Click **Continue**.
5. The scan will now be relative to the flat plate, effectively removing the bed's curvature from the data.

Set **Plate lift Z = 0** to disable the pause (e.g., when scanning an object placed directly on a flat bed).

### 5. Running the Scan
- Click **Start Scan**. A confirmation dialog summarizes the grid, the zone, and whether homing / plate lift will happen.
- **Homing**: the first scan of a session performs `G28 W` (Z-homing only — plain `G28` runs auto bed leveling and is never used). Subsequent scans skip homing.
- **Monitor**: the live height map (NANOMAP colormap) fills in row by row, and the progress bar shows `done/total` with a live ETA.
- **Abort** stops the scan at the next point boundary; the head lifts to clearance and any points already measured are still saved to CSV.

### 6. Analyzing & Saving Results
- **Live View**: the height map updates in real time. Areas where the probe did not make contact are shown as gaps (NaN) and are excluded from the colorbar range.
- **CSV Export**: each scan is automatically saved to the `scans/` directory as `scan_YYYYmmdd_HHMMSS.csv` with $X, Y, Z$ columns (empty Z = no contact).
- **Image Export**: click **Save PNG** to export the final height map.

### 7. Comparing Scans: Bed-Map Overlay and Coarse → Fine Workflow
- **Overlay**: every finished scan is drawn onto the bed map with a shared, autoscaled colorbar, so you can see where each scan was and compare heights across zones. **Clear map** removes all overlays.
- **Coarse → fine**: run a fast coarse scan (large pitch) over a big area, then select a smaller zone with a fine pitch. When the fine scan starts, its not-yet-measured cells are pre-filled with the nearest values from the previous scan, so you immediately see the underlying data while the fine scan fills in.
