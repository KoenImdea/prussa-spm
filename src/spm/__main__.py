"""Console entry point: 3d-print-spm [--demo]"""

import argparse

from .gui import main

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Prusa SPM Scanner GUI")
    ap.add_argument(
        "--demo",
        action="store_true",
        help="demo mode: fast hover flight over the known demo surface, "
        "z values from the surface model (GUI looks like a normal scan)",
    )
    args = ap.parse_args()
    main(demo=args.demo)
