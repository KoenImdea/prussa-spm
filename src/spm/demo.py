"""Demo mode: known-surface model + fast hover trajectory.

Used by :class:`spm.scan.ScanJob` when ``ScanConfig.demo`` is True. The tip
flies the scan grid hovering a margin above the *known* demo-surface
heightmap (calibrated against the coarse scan) — no G30, no contact — while
the GUI is fed z values from the model, so a demo run looks like a real
scan but runs at maximum speed.

Surface model (bed coordinates, absolute z, same frame as G30 readings):
  on plate:  plane(x, y) + heightmap_sample(x, y)
             where the plane is fitted so the model matches the measured
             coarse-scan z values on the plate (glue thickness + warp).
  off plate: bilinear interpolation of the measured coarse-scan bed heights.

Safety: physical hover heights get an extra ``SAFETY_OFFSET_MM`` on top of
the model (the model under-predicts in places; calibration TODO). The z
*data* reported to the GUI/CSV does NOT include this offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import spiepy
from scipy.ndimage import map_coordinates

# --- plate pose (from scripts/estimate_position.py) ------------------------
PLATE_CX = 120.228       # mm, bed coords
PLATE_CY = 96.999        # mm
PLATE_THETA_DEG = 0.525  # deg, ccw positive

# --- design / data constants -----------------------------------------------
HEIGHTMAP_PX = 2000
PX_PER_MM = 10.0
PLATE_HALF = 100.0

DEFAULT_HEIGHTMAP = "demo_surface/heightmap_010.npy"
DEFAULT_COARSE = "scans/scan_20260921_135831.csv"

SAFE_Z = 12.0            # initial / final clearance, mm
MIN_HOVER = 0.8          # absolute floor for hover height, mm
SAFETY_OFFSET_MM = 2.0   # TEMP: added after test-point crash; surface model
                         # under-predicts in places (calibration issue, TODO)
DEMO_MARGIN = 1.5        # hover margin above the surface, mm
SEGMENT_SAMPLE_MM = 2.0  # sampling distance for corridor max

# stock MK3S+ max feedrates, used for the time estimate only
XY_SPEED_MM_S = 300.0 / 60.0
Z_SPEED_MM_S = 120.0 / 60.0
MOVE_OVERHEAD_S = 0.3    # accel/decel + ok-handshake slack per move


class SurfaceModel:
    """Absolute bed-frame z of the demo surface (plate + bare bed).

    Call it as ``z = surface(x, y)``; accepts scalars or arrays and returns
    an array of model heights in mm (no safety offset).
    """

    def __init__(
        self,
        heightmap_path: str = DEFAULT_HEIGHTMAP,
        coarse_path: str = DEFAULT_COARSE,
    ):
        H = np.load(heightmap_path).astype(np.float64)
        H[~np.isfinite(H)] = np.nan
        Hf, _ = spiepy.flatten.xy(np.where(np.isnan(H), 0.0, H), np.isnan(H))
        self._hmap = np.where(np.isnan(Hf), np.nanmedian(Hf), Hf)

        d = np.genfromtxt(coarse_path, delimiter=",", names=True)
        self._cxs, self._cys = np.unique(d["x_mm"]), np.unique(d["y_mm"])
        CZ = np.full((self._cys.size, self._cxs.size), np.nan)
        CZ[
            np.searchsorted(self._cys, d["y_mm"]),
            np.searchsorted(self._cxs, d["x_mm"]),
        ] = d["z_mm"]

        X, Y = np.meshgrid(self._cxs, self._cys)
        m = self._on_plate(X, Y) & np.isfinite(CZ)
        resid = CZ[m] - self._hmap_at(X[m], Y[m])
        A = np.column_stack([np.ones(m.sum()), X[m], Y[m]])
        self._coef, *_ = np.linalg.lstsq(A, resid, rcond=None)
        self._zbed = np.where(m, np.nan, CZ)  # measured bed, plate removed

    # -- geometry -----------------------------------------------------------
    def _plate_local(self, x, y):
        """Bed coords (mm) -> plate-local coords (mm, 0..200 from the corner)."""
        t = np.radians(PLATE_THETA_DEG)
        dx, dy = np.asarray(x, dtype=float) - PLATE_CX, np.asarray(y, dtype=float) - PLATE_CY
        lx = dx * np.cos(t) + dy * np.sin(t) + PLATE_HALF
        ly = -dx * np.sin(t) + dy * np.cos(t) + PLATE_HALF
        return lx, ly

    def _on_plate(self, x, y) -> np.ndarray:
        lx, ly = self._plate_local(x, y)
        return (
            (lx >= 0) & (lx <= 2 * PLATE_HALF) & (ly >= 0) & (ly <= 2 * PLATE_HALF)
        )

    def _hmap_at(self, x, y) -> np.ndarray:
        lx, ly = self._plate_local(x, y)
        return map_coordinates(
            self._hmap, [ly.ravel() * PX_PER_MM, lx.ravel() * PX_PER_MM], order=1
        ).reshape(np.shape(np.asarray(x, dtype=float)))

    # -- model --------------------------------------------------------------
    def __call__(self, x, y) -> np.ndarray:
        """Model surface height (mm, bed frame) at bed coords (x, y)."""
        x, y = np.atleast_1d(np.asarray(x, dtype=float)), np.atleast_1d(
            np.asarray(y, dtype=float)
        )
        c = self._coef
        h = np.where(
            self._on_plate(x, y), c[0] + c[1] * x + c[2] * y + self._hmap_at(x, y), np.nan
        )
        need = np.isnan(h)
        if need.any():
            ix = (x[need] - self._cxs[0]) / (self._cxs[-1] - self._cxs[0]) * (
                self._cxs.size - 1
            )
            iy = (y[need] - self._cys[0]) / (self._cys[-1] - self._cys[0]) * (
                self._cys.size - 1
            )
            h[need] = map_coordinates(self._zbed, [iy, ix], order=1, cval=np.nan)
        if np.isnan(h).any():
            raise ValueError(
                "point outside the measured coarse-scan area (0-210 mm); "
                "shrink the scan zone"
            )
        return np.maximum(h, MIN_HOVER)


@dataclass
class DemoTrajectory:
    """G-code moves for a fast hover flight over the given points.

    ``points`` is a list of ``(x, y, moves)`` where ``moves`` are the
    ``(gcode, est_s)`` tuples to fly from the previous hover to this point's
    hover height. Fly ``preamble``, then each point's moves (followed by
    ``M400``), then ``postamble``.
    """

    preamble: list[tuple[str, float]]
    points: list[tuple[float, float, list[tuple[str, float]]]]
    postamble: list[tuple[str, float]]
    est_s: float
    hovers: list[float] = field(default_factory=list)
    corridor_max: float = 0.0


def build_trajectory(
    points: list[tuple[float, float]], surface: SurfaceModel, margin: float
) -> DemoTrajectory:
    """Plan hover heights and per-segment corridor heights for ``points``.

    The corridor for a segment is the max model surface along it plus
    ``SAFETY_OFFSET_MM + margin``, so the tip only lifts as much as the
    terrain requires (this also clears the plate edge wall).
    """
    lift_all = SAFETY_OFFSET_MM + margin
    hovers = [float(surface(x, y)[0]) + lift_all for x, y in points]
    corridors: list[float] = []
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
        n = max(2, int(round(np.hypot(x2 - x1, y2 - y1) / SEGMENT_SAMPLE_MM)) + 1)
        seg = [
            float(surface(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)[0])
            for t in np.linspace(0, 1, n)
        ]
        corridors.append(max(seg) + lift_all)
    corridors.append(hovers[-1])

    preamble: list[tuple[str, float]] = [
        ("G90", 0.0),
        (f"G1 Z{SAFE_Z:g} F6000", SAFE_Z / Z_SPEED_MM_S),
    ]
    postamble: list[tuple[str, float]] = []
    pts: list[tuple[float, float, list[tuple[str, float]]]] = []
    t = sum(dt for _, dt in preamble) + MOVE_OVERHEAD_S
    z = SAFE_Z
    px = py = 0.0
    first = True
    for (x, y), hover, corr in zip(points, hovers, corridors):
        moves: list[tuple[str, float]] = []
        lift = max(z, corr)
        if lift > z + 0.01:
            dt = (lift - z) / Z_SPEED_MM_S
            moves.append((f"G1 Z{lift:.2f} F6000", dt))
            t += dt + MOVE_OVERHEAD_S
            z = lift
        dt = 0.0 if first else np.hypot(x - px, y - py) / XY_SPEED_MM_S
        moves.append((f"G1 X{x:.2f} Y{y:.2f} F6000", dt))
        t += dt + MOVE_OVERHEAD_S
        px, py, first = x, y, False
        dt = (z - hover) / Z_SPEED_MM_S
        moves.append((f"G1 Z{hover:.2f} F6000", dt))
        t += dt + MOVE_OVERHEAD_S
        z = hover
        pts.append((x, y, moves))
    dt = (SAFE_Z - z) / Z_SPEED_MM_S
    postamble.append((f"G1 Z{SAFE_Z:g} F6000", dt))
    t += dt + MOVE_OVERHEAD_S
    postamble.append(("M400", 0.0))
    return DemoTrajectory(
        preamble=preamble,
        points=pts,
        postamble=postamble,
        est_s=t,
        hovers=hovers,
        corridor_max=max(corridors),
    )
