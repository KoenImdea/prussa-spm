#!/usr/bin/env python
"""Fast "fake coarse scan" demo trajectory for the Prusa SPM (CLI driver).

Flies the demo hover trajectory from :mod:`spm.demo` — the tip hovers a
margin above the known demo-surface heightmap, no G30, no contact. The same
trajectory is used by the GUI in ``--demo`` mode.

Usage:
  uv run python scripts/demo_scan.py --dry-run            # print G-code + time estimate
  uv run python scripts/demo_scan.py --test-point         # single hover at plate center
  uv run python scripts/demo_scan.py --port /dev/cu.usbmodem21201   # fly it
"""

from __future__ import annotations

import argparse
import time

from spm.demo import (
    DEFAULT_COARSE,
    DEFAULT_HEIGHTMAP,
    DEMO_MARGIN,
    MOVE_OVERHEAD_S,
    PLATE_CX,
    PLATE_CY,
    SAFE_Z,
    SurfaceModel,
    build_trajectory,
)


def serpentine(cx, cy, nx, ny, pitch):
    pts = []
    x0, y0 = cx - (nx - 1) * pitch / 2, cy - (ny - 1) * pitch / 2
    for iy in range(ny):
        row = [(x0 + ix * pitch, y0 + iy * pitch) for ix in range(nx)]
        pts += row if iy % 2 == 0 else row[::-1]
    return pts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--heightmap", default=None)
    ap.add_argument("--coarse", default=None)
    ap.add_argument("--port", default="/dev/cu.usbmodem21201")
    ap.add_argument("--nx", type=int, default=10)
    ap.add_argument("--ny", type=int, default=10)
    ap.add_argument("--pitch", type=float, default=10.0)
    ap.add_argument("--margin", type=float, default=DEMO_MARGIN,
                    help="hover margin above the surface, mm")
    ap.add_argument("--no-home", action="store_true", help="skip G28 W")
    ap.add_argument("--dry-run", action="store_true", help="print G-code, do not connect")
    ap.add_argument("--test-point", action="store_true", help="single hover at plate center")
    args = ap.parse_args()

    surface = SurfaceModel(
        heightmap_path=args.heightmap or DEFAULT_HEIGHTMAP,
        coarse_path=args.coarse or DEFAULT_COARSE,
    )

    if args.test_point:
        pts = [(PLATE_CX, PLATE_CY)]
    else:
        pts = serpentine(PLATE_CX, PLATE_CY, args.nx, args.ny, args.pitch)

    traj = build_trajectory(pts, surface, args.margin)
    print(f"points: {len(pts)}, est. duration: {traj.est_s / 60:.1f} min")
    print(f"hover range: {min(traj.hovers):.2f} .. {max(traj.hovers):.2f} mm, "
          f"corridor max: {traj.corridor_max:.2f} mm")

    if args.dry_run:
        t = 0.0
        for cmd, dt in traj.preamble:
            t += dt + (MOVE_OVERHEAD_S if cmd.startswith("G1") else 0.0)
            print(f"  {t:8.1f}s  {cmd}")
        for x, y, moves in traj.points:
            for cmd, dt in moves:
                t += dt + MOVE_OVERHEAD_S
                print(f"  {t:8.1f}s  {cmd}")
        for cmd, dt in traj.postamble:
            t += dt + (MOVE_OVERHEAD_S if cmd.startswith("G1") else 0.0)
            print(f"  {t:8.1f}s  {cmd}")
        return

    # -- live flight --
    from spm.prusa import PrusaScanner

    sc = PrusaScanner(args.port)
    sc.connect()
    try:
        if not args.no_home:
            print("homing (G28 W)...")
            sc.home()
        t0 = time.monotonic()
        for cmd, _ in traj.preamble:
            sc.send_command(cmd)
        for i, (x, y, moves) in enumerate(traj.points):
            for cmd, _ in moves:
                sc.send_command(cmd)
            sc.send_command("M400")
            if i % 20 == 0:
                print(f"  [{i}/{len(traj.points)}] ({x:.1f}, {y:.1f})  "
                      f"({time.monotonic() - t0:.0f}s elapsed)")
        for cmd, _ in traj.postamble:
            sc.send_command(cmd)
        print(f"done in {time.monotonic() - t0:.0f}s")
    except KeyboardInterrupt:
        print("\naborted — lifting to safe Z")
        try:
            sc.raise_z(SAFE_Z)
        finally:
            sc.close()
        raise
    except Exception:
        try:
            sc.raise_z(SAFE_Z)
        finally:
            sc.close()
        raise
    else:
        sc.close()


if __name__ == "__main__":
    main()
