# Setup Guide

This guide describes how to set up the hardware and software required to turn your Prusa MK3S+ into a Scanning Probe Microscope.

## Hardware Setup

### The Cantilever
The core of the SPM is the cantilever. You need to mount a flexible beam (e.g., a thin strip of spring steel or a plastic cantilever) to the print head such that:
1. The tip of the cantilever is the point of contact with the sample.
2. When the cantilever bends upwards upon contact, it triggers the **PINDA sensor**.

**Calibration**:
- Ensure the cantilever is mounted rigidly enough that it doesn't vibrate, but flexibly enough to trigger the PINDA sensor without putting excessive force on the sample.
- Test the trigger by manually pushing the cantilever up; the PINDA sensor should change state (verified via `M119` command).

### Sample Placement
Place your sample on the heatbed. Use a flat reference plate (like a piece of glass or a precision-ground metal plate) if you need to compensate for bed warping — see the **Plate lift Z** option in [usage.md](usage.md).

**Note**: use a **solid** plate. A perforated one (e.g., a PCB) can be poked through by the probe, which reads the surface below instead.

### Sensor Offset
The PINDA sensor is not directly under the nozzle. For safety, the scannable area is restricted to **X = 0–210 mm** (the bed map shows the 210–250 mm zone hatched as "no scan").

## Software Setup

### Environment
The project is managed with [uv](https://docs.astral.sh/uv/) and requires **Python 3.14+**:

```bash
uv sync          # creates .venv and installs all dependencies
```

Without uv, use a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
.\venv\Scripts\activate   # Windows
pip install pyserial numpy matplotlib spiepy
```

### Finding the Serial Port
- macOS: `ls /dev/cu.usbmodem*` (or System Settings → Modems)
- Linux: `ls /dev/ttyUSB*` or `ls /dev/ttyACM*`
- Windows: Device Manager → Ports (COMx)

### Running the Application

```bash
uv run main.py
# or, without uv:
python main.py
# or, using the installed script:
uv run 3d-print-spm
```
