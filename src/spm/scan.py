"""Scan job: grid generation and the background scan loop.

The job runs in its own thread and communicates with the GUI exclusively
through a queue of events:

    ("status",  msg)
    ("grid",    nx, ny, x0, x1, y0, y1)
    ("point",   ix, iy, x, y, z_or_None)
    ("done",    csv_path_or_None)
    ("aborted", csv_path_or_None)
    ("error",   msg)
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Queue
from typing import Any

import numpy as np

from .prusa import PrusaScanner


@dataclass
class ScanConfig:
    """
    Configuration for a surface scan job.

    Attributes
    ----------
    x0 : float
        Start X coordinate of the scan area in mm.
    x1 : float
        End X coordinate of the scan area in mm.
    y0 : float
        Start Y coordinate of the scan area in mm.
    y1 : float
        End Y coordinate of the scan area in mm.
    pitch : float
        Distance between scan points in mm.
    z_clearance : float
        Z height used for safe travel between points. Defaults to 8.0.
    step : float
        Step size for manual probe descent. Defaults to 0.1.
    max_depth : float
        Maximum Z distance to descend during manual probing. Defaults to 8.0.
    plate_lift : float
        Z height to lift after homing to allow placement of a flat plate.
        0 = off. Defaults to 0.0.
    probe_mode : str
        Probing method: 'g30' (fast, firmware) or 'manual' (step-down).
        Defaults to 'g30'.
    """
    x0: float
    x1: float
    y0: float
    y1: float
    pitch: float
    z_clearance: float = 8.0
    step: float = 0.1
    max_depth: float = 8.0
    plate_lift: float = 0.0  # Z height to lift after homing to place a flat
    # plate (workaround for warped bed); 0 = off
    probe_mode: str = "g30"  # "g30" (fast, firmware descent) or "manual"

    def grid(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Generate the X and Y coordinate arrays for the scan grid.

        Returns
        -------
        tuple of np.ndarray
            A tuple (xs, ys) containing the coordinate arrays.
        """
        xs = np.arange(self.x0, self.x1 + self.pitch / 2, self.pitch)
        ys = np.arange(self.y0, self.y1 + self.pitch / 2, self.pitch)
        return xs, ys

    @property
    def nx(self) -> int:
        """Number of points along the X axis."""
        return len(self.grid()[0])

    @property
    def ny(self) -> int:
        """Number of points along the Y axis."""
        return len(self.grid()[1])

    @property
    def total(self) -> int:
        """Total number of points in the scan grid."""
        return self.nx * self.ny



def save_csv(results: list[tuple[float, float, float | None]]) -> Path:
    """
    Save scan results to a CSV file in the 'scans' directory.

    Parameters
    ----------
    results : list of tuple
        A list of (x, y, z) tuples. Z can be None if no contact was made.

    Returns
    -------
    Path
        The path to the created CSV file.
    """
    out_dir = Path("scans")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"scan_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x_mm", "y_mm", "z_mm"])
        for x, y, z in results:
            w.writerow([f"{x:.3f}", f"{y:.3f}", "" if z is None else f"{z:.4f}"])
    return path


class ScanJob(threading.Thread):
    """
    Worker thread that executes the scanning process.

    The job runs in its own thread to keep the GUI responsive and communicates
    with the GUI via a queue of events.

    Attributes
    ----------
    scanner : PrusaScanner
        The scanner instance to control the printer.
    cfg : ScanConfig
        The configuration for the scan job.
    q : Queue
        Queue for sending events back to the GUI.
    abort_event : threading.Event
        Event to signal the job to abort.
    continue_event : threading.Event
        Event to signal the job to continue after a pause (e.g., plate lift).
    do_home : bool
        Whether to perform a Z-homing (G28 W) before starting.
    """
    def __init__(
        self,
        scanner: PrusaScanner,
        cfg: ScanConfig,
        q: Queue[tuple[str, ...]],
        abort_event: threading.Event,
        continue_event: threading.Event,
        do_home: bool = True,
    ):
        super().__init__(daemon=True)
        self.scanner = scanner
        self.cfg = cfg
        self.q = q
        self.abort_event = abort_event
        self.continue_event = continue_event
        self.do_home = do_home

    def run(self) -> None:
        """
        Main execution loop of the scan job.

        Handles homing, plate lift pause, grid traversal, and data saving.
        Sends events to the GUI queue: ('status', 'grid', 'point', 'done', 'aborted', 'error').
        """
        cfg = self.cfg
        try:
            if self.do_home:
                self.q.put(("status", "Homing (G28 W — no bed leveling)..."))
                self.scanner.home()
            else:
                self.q.put(("status", "Skipping home (already homed after connect)."))
            self.scanner.send_command("G90")

            aborted = False
            if cfg.plate_lift > 0:
                self.q.put(
                    ("status", f"Lifting Z to {cfg.plate_lift:g} mm — place the flat plate now.")
                )
                self.scanner.raise_z(cfg.plate_lift, feed=3000)
                self.q.put(("pause", "Plate in place? Click Continue."))
                while not self.continue_event.wait(timeout=0.5):
                    if self.abort_event.is_set():
                        aborted = True
                        break
                if aborted:
                    self.q.put(("aborted", None))
                    return

            self.scanner.raise_z(cfg.z_clearance)

            xs, ys = cfg.grid()
            nx, ny = xs.size, ys.size
            total = nx * ny
            self.q.put(("grid", nx, ny, float(xs[0]), float(xs[-1]), float(ys[0]), float(ys[-1])))
            self.q.put(("status", f"Scanning {total} points..."))

            results: list[tuple[float, float, float | None]] = []
            aborted = False
            for iy, y in enumerate(ys):
                if self.abort_event.is_set():
                    aborted = True
                    break
                for ix, x in enumerate(xs):
                    if self.abort_event.is_set():
                        aborted = True
                        break
                    if cfg.probe_mode == "g30":
                        z = self.scanner.probe_g30(float(x), float(y), cfg.z_clearance)
                    else:
                        z = self.scanner.probe_point(
                            float(x),
                            float(y),
                            cfg.z_clearance,
                            step=cfg.step,
                            max_depth=cfg.max_depth,
                        )
                    results.append((float(x), float(y), z))
                    self.q.put(
                        (
                            "point",
                            ix,
                            iy,
                            float(x),
                            float(y),
                            None if z is None else float(z),
                        )
                    )
                    self.q.put(("status", f"[{len(results)}/{total}] X:{x:g} Y:{y:g} Z:{'--' if z is None else f'{z:.2f}'}"))

            # Always lift before finishing / saving
            try:
                self.scanner.raise_z(cfg.z_clearance)
            except Exception:
                pass

            csv_path = save_csv(results) if results else None
            if aborted:
                self.q.put(("aborted", str(csv_path) if csv_path else None))
            else:
                self.q.put(("done", str(csv_path) if csv_path else None))
        except Exception as e:  # noqa: BLE001 - report any failure to the GUI
            self.q.put(("error", f"{type(e).__name__}: {e}"))
