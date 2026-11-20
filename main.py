"""Standalone entry point: python main.py [--demo]"""

import argparse

from spm.gui import main

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
