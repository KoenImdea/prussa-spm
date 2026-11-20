#!/usr/bin/env python
"""Estimate the position (center + rotation) of the demo surface on the Prusa bed.

Strategy:
  1. Coarse scan (10 mm pitch, 0-210 mm): segment plate vs bed, fit the
     left and top edge lines -> rough plate center + rough rotation.
  2. Fine scan (2 mm pitch): flatten both the scan and a heightmap window
     (spiepy plane removal), then search rotation in [-3, +3] deg and use
     skimage phase_cross_correlation for the sub-pixel translation.

Convention: arrays have shape (ny, nx); axis0 = y (rows), axis1 = x (cols).
Reported rotation is the plate rotation in bed coordinates (x right, y up),
counter-clockwise positive, in degrees.

Usage:
  uv run python scripts/estimate_position.py
  uv run python scripts/estimate_position.py --coarse scans/a.csv --fine scans/b.csv
"""

import argparse
from pathlib import Path

import numpy as np
import spiepy
from scipy.ndimage import map_coordinates
from scipy.signal import correlate2d
from skimage.filters import threshold_otsu

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --- design constants (demo surface) -------------------------------------
HEIGHTMAP_PX = 2000          # heightmap_010.npy is 2000 x 2000 px
HEIGHTMAP_MM = 200.0         # printed plate is 200 x 200 mm
PX_PER_MM = HEIGHTMAP_PX / HEIGHTMAP_MM
PLATE_HALF = HEIGHTMAP_MM / 2.0   # half side, mm
HMAP_CENTER_PX = HEIGHTMAP_PX / 2.0  # plate center in heightmap px (1000, 1000)

ROT_RANGE_DEG = 3.0          # fine-stage rotation search range
ROT_STEP_DEG = 0.25          # coarse rotation step
ROT_REFINE_STEP_DEG = 0.05   # refinement step around the best angle
SHIFT_SEARCH_MM = 15.0       # translation search half-window (mm)


def load_scan(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load a scan CSV (x_mm, y_mm, z_mm) into a 2D grid Z (ny, nx)."""
    d = np.genfromtxt(path, delimiter=",", names=True)
    xs, ys = np.unique(d["x_mm"]), np.unique(d["y_mm"])
    Z = np.full((len(ys), len(xs)), np.nan)
    Z[np.searchsorted(ys, d["y_mm"]), np.searchsorted(xs, d["x_mm"])] = d["z_mm"]
    Z[~np.isfinite(Z)] = np.nan
    return xs, ys, Z


def flatten(Z: np.ndarray) -> np.ndarray:
    """Remove a plane (spiepy), treating NaNs as invalid."""
    Zf, _ = spiepy.flatten.xy(np.where(np.isnan(Z), 0.0, Z), np.isnan(Z))
    return Zf


# --- stage 1: coarse ------------------------------------------------------
def coarse_estimate(xs, ys, Z):
    """Segment plate vs bed and fit the left and top edges.

    Returns (cx, cy, theta_deg) of the plate in bed coordinates.
    """
    thr = threshold_otsu(Z[np.isfinite(Z)])
    mask = Z > thr

    w = PLATE_HALF
    # left edge: first plate column in each row -> x_left(y) = a + b*y
    lx, ly = [], []
    for i in range(len(ys)):
        cols = np.where(mask[i])[0]
        if len(cols) >= 3:
            lx.append(xs[cols[0]])
            ly.append(ys[i])
    b, a = np.polyfit(ly, lx, 1)  # x = a + b*y

    # top edge: last plate row in each column -> y_top(x) = c + d*x
    tx, ty = [], []
    for j in range(len(xs)):
        rows = np.where(mask[:, j])[0]
        if len(rows) >= 3:
            tx.append(xs[j])
            ty.append(ys[rows[-1]])
    d, c = np.polyfit(tx, ty, 1)  # y = c + d*x

    # For a plate rotated by theta (ccw) about (cx, cy):
    #   left edge:  b = -tan(theta), a = cx - w cos(theta) + cy b + w b sin(theta)
    #   top edge:   d = +tan(theta), c = cy + w cos(theta) - cx d - w d sin(theta)
    theta = 0.5 * (np.arctan(d) - np.arctan(b))
    s, co = np.sin(theta), np.cos(theta)
    cx, cy = np.linalg.solve(
        np.array([[1.0, -b], [-d, 1.0]]),
        np.array([a + w * co + w * b * s, c - w * co - w * d * s]),
    )
    return cx, cy, np.degrees(theta), mask, thr


# --- stage 2: fine --------------------------------------------------------
def sample_heightmap(Hf, xs, ys, cx, cy, alpha_deg):
    """Sample the (flattened) heightmap on the fine-scan grid.

    The heightmap is interpreted in bed coordinates with the plate center at
    (cx, cy), then rotated by `alpha_deg` in ndimage convention (i.e.
    -alpha_deg in bed coordinates, ccw positive).
    """
    # fine-scan bed coords -> heightmap px
    hx = (xs - (cx - PLATE_HALF)) * PX_PER_MM
    hy = (ys - (cy - PLATE_HALF)) * PX_PER_MM
    # rotate around the plate center in heightmap px by alpha (ndimage convention)
    a = np.radians(alpha_deg)
    ca, sa = np.cos(a), np.sin(a)
    dx = hx - HMAP_CENTER_PX   # (nx,)  x offsets in heightmap px
    dy = hy - HMAP_CENTER_PX   # (ny,)  y offsets in heightmap px
    # rotate each grid point around the plate center (ndimage convention)
    IX = dx[None, :] * ca + dy[:, None] * sa + HMAP_CENTER_PX   # (ny, nx)
    IY = -dx[None, :] * sa + dy[:, None] * ca + HMAP_CENTER_PX  # (ny, nx)
    return map_coordinates(Hf, [IY.ravel(), IX.ravel()], order=1).reshape(len(ys), len(xs))


def _parabolic_offset(c, i, j, axis):
    """Sub-pixel peak offset along `axis` via 3-point parabola."""
    if i == 0 or i == c.shape[0] - 1 or j == 0 or j == c.shape[1] - 1:
        return 0.0
    if axis == 0:
        ym, y0, yp = c[i - 1, j], c[i, j], c[i + 1, j]
    else:
        ym, y0, yp = c[i, j - 1], c[i, j], c[i, j + 1]
    denom = ym - 2.0 * y0 + yp
    return 0.0 if abs(denom) < 1e-12 else 0.5 * (ym - yp) / denom


def fine_estimate(Hf, xs, ys, Zf, cx, cy):
    """Search rotation in [-3, +3] deg; correlate2d for the translation.

    For each candidate rotation, the heightmap is sampled on the fine-scan
    grid (anchored at the current center estimate) and directly
    cross-correlated with the fine scan over a +/- SHIFT_SEARCH_MM window.

    Returns (cx, cy, theta_deg, score, shift_mm, rmse).
    """
    pitch = xs[1] - xs[0]  # mm per fine-grid pixel (assumed isotropic)
    ref = Zf - np.nanmedian(Zf)
    ny, nx = ref.shape
    nwin = int(round(SHIFT_SEARCH_MM / pitch))  # half-window in fine px

    def evaluate(alpha_deg):
        # ndimage rotation alpha corresponds to -alpha in bed coords
        img = sample_heightmap(Hf, xs, ys, cx, cy, -alpha_deg)
        img = img - np.nanmedian(img)
        corr = correlate2d(img, ref, mode="full")
        # restrict to +/- nwin px around zero lag (avoids spurious far peaks)
        sl = (slice(ny - 1 - nwin, ny - 1 + nwin + 1),
              slice(nx - 1 - nwin, nx - 1 + nwin + 1))
        c = corr[sl]
        py, px = np.unravel_index(np.argmax(c), c.shape)
        dy = py - nwin + _parabolic_offset(c, py, px, 0)
        dx = px - nwin + _parabolic_offset(c, py, px, 1)
        return dy, dx, float(c[py, px])

    def search(alphas):
        best = None
        for al in alphas:
            dy, dx, peak = evaluate(al)
            if best is None or peak > best[2]:
                best = (al, (dy, dx), peak)
        return best

    # coarse pass
    alphas = np.arange(-ROT_RANGE_DEG, ROT_RANGE_DEG + 1e-9, ROT_STEP_DEG)
    alpha, (dy, dx), peak = search(alphas)
    # refinement pass around the best angle
    alphas = np.arange(alpha - ROT_STEP_DEG / 2, alpha + ROT_STEP_DEG / 2 + 1e-9,
                       ROT_REFINE_STEP_DEG)
    alpha, (dy, dx), peak = search(alphas)

    # correlate2d peak = displacement of img relative to ref = c_est - c_true
    shift_mm = np.array([dx, dy]) * pitch  # (dx, dy) in mm
    cx_new, cy_new = cx - shift_mm[0], cy - shift_mm[1]

    # residual after alignment
    aligned = sample_heightmap(Hf, xs, ys, cx_new, cy_new, -alpha)
    diff = aligned - Zf
    rmse = float(np.sqrt(np.nanmean(diff**2)))
    return cx_new, cy_new, float(alpha), float(peak), shift_mm, rmse


# --- main -----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coarse", default="scans/scan_20260921_135831.csv")
    ap.add_argument("--fine", default="scans/scan_20260921_190220.csv")
    ap.add_argument("--heightmap", default="demo_surface/heightmap_010.npy")
    ap.add_argument("--out", default="scripts/position_estimate.png")
    args = ap.parse_args()

    # --- load + flatten ---
    cxs, cys, CZ = load_scan(args.coarse)
    fxs, fys, FZ = load_scan(args.fine)
    FZf = flatten(FZ)

    H = np.load(args.heightmap).astype(np.float64)
    H[~np.isfinite(H)] = np.nan
    Hf = flatten(H)
    Hf_fill = np.where(np.isnan(Hf), np.nanmedian(Hf), Hf)  # for sampling

    # --- stage 1: coarse ---
    cx0, cy0, th0, mask, thr = coarse_estimate(cxs, cys, CZ)
    print(f"[coarse] plate center: ({cx0:.2f}, {cy0:.2f}) mm, rotation: {th0:.3f} deg")

    # --- stage 2: fine ---
    cx, cy, theta, peak, shift_mm, rmse = fine_estimate(
        Hf_fill, fxs, fys, FZf, cx0, cy0
    )
    print(f"[fine  ] plate center: ({cx:.3f}, {cy:.3f}) mm, rotation: {theta:.4f} deg")
    print(f"[fine  ] PCC shift: ({shift_mm[0]:+.3f}, {shift_mm[1]:+.3f}) mm, "
          f"peak: {peak:.4f}, alignment RMSE: {rmse:.4f} mm")

    # --- visualize ---
    fig, ax = plt.subplots(1, 3, figsize=(18, 6))

    # 1: coarse scan with segmentation and estimated plate outline
    im0 = ax[0].pcolormesh(cxs, cys, CZ, cmap="viridis", shading="auto")
    ax[0].contour(cxs, cys, mask.astype(float), levels=[0.5], colors="w", linewidths=1)
    for cx_, cy_, lab in [(cx0, cy0, "coarse"), (cx, cy, "fine")]:
        t = np.radians(theta) if lab == "fine" else np.radians(th0)
        corners = np.array([[-PLATE_HALF, -PLATE_HALF], [PLATE_HALF, -PLATE_HALF],
                            [PLATE_HALF, PLATE_HALF], [-PLATE_HALF, PLATE_HALF],
                            [-PLATE_HALF, -PLATE_HALF]])
        R = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        pts = corners @ R.T + [cx_, cy_]
        ax[0].plot(pts[:, 0], pts[:, 1], "--",
                   color="red" if lab == "fine" else "yellow",
                   label=f"plate outline ({lab})")
    ax[0].set_title(f"coarse scan (thr={thr:.2f} mm)")
    ax[0].legend(loc="lower right")

    # 2: fine scan (flattened)
    im1 = ax[1].pcolormesh(fxs, fys, FZf, cmap="viridis", shading="auto")
    ax[1].set_title("fine scan (flattened)")

    # 3: heightmap window at the final estimate
    aligned = sample_heightmap(Hf_fill, fxs, fys, cx, cy, -theta)
    im2 = ax[2].pcolormesh(fxs, fys, aligned, cmap="viridis", shading="auto")
    ax[2].set_title(f"heightmap window (aligned, RMSE={rmse:.3f} mm)")

    for a in ax:
        a.set_xlabel("x mm"); a.set_ylabel("y mm"); a.set_aspect("equal")
    for im, a in [(im0, ax[0]), (im1, ax[1]), (im2, ax[2])]:
        fig.colorbar(im, ax=a)
    fig.tight_layout()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
