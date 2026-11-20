"""Tkinter GUI: bed map with zone selector, scan controls, live image."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

import matplotlib
matplotlib.use("TkAgg")
import numpy as np
import spiepy  # registers the NANOMAP colormap (used for all height maps)
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from .prusa import PrusaError, PrusaScanner
from .scan import ScanConfig, ScanJob

BED_X, BED_Y = 250.0, 210.0  # MK3S+ bed size in mm
# The sensor is not directly under the nozzle: for safety only X 0..210 is
# scannable (the sensor would hang over the bed edge beyond that).
SCAN_X_MAX = 210.0
MARGIN = 5.0  # safety margin kept inside the bed when selecting a zone


class App:
    """
    Tkinter application for controlling the Prusa SPM Scanner.

    Provides a visual interface to select a scan zone on the printer bed,
    configure scan parameters, control the scanning process in real-time,
    and visualize the resulting surface height map.

    Attributes
    ----------
    scanner : PrusaScanner or None
        The active printer connection.
    job : ScanJob or None
        The current active scan job.
    homed : bool
        Flag indicating if the printer has been homed.
    q : queue.Queue
        Queue for events from the ScanJob thread.
    abort_event : threading.Event
        Event to stop the active scan.
    continue_event : threading.Event
        Event to resume scan after a pause.
    image : np.ndarray or None
        The height map data for the current scan.
    last_csv : str or None
        Path to the most recently saved CSV file.
    completed_scans : list of dict
        History of completed scans for overlay on the bed map.
    """
    def __init__(self, root: tk.Tk, demo: bool = False):
        self.root = root

        # demo mode: scans fly the fast hover trajectory over the known
        # demo surface and report model z values; the GUI is intentionally
        # identical to a real scan (no visible demo indicator).
        self.demo = demo

        root.title("Prusa SPM Scanner")
        root.minsize(760, 760)

        self.scanner: PrusaScanner | None = None
        self.job: ScanJob | None = None
        self.homed = False  # True after a successful home; reset on disconnect/error
        self.q: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.abort_event = threading.Event()
        self.continue_event = threading.Event()

        self.image: np.ndarray | None = None
        self.scan_start = 0.0
        self.last_csv: str | None = None
        self.last_grid: tuple[int, int, float, float, float, float] | None = None
        self.completed_scans: list[dict[str, Any]] = []
        self.map_images: list[Any] = []
        self.map_cbar: Any = None

        self._build_bed_map()
        self._build_controls()
        self._build_image()
        self._build_statusbar()

        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------

    def _build_bed_map(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Scan zone (drag on the bed)")
        frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.map_fig = Figure(figsize=(6.8, 4.4), dpi=100)
        self.map_ax = self.map_fig.add_subplot(111)
        self.map_ax.set_xlim(0, BED_X)
        self.map_ax.set_ylim(0, BED_Y)
        self.map_ax.set_aspect("equal")
        self.map_ax.add_patch(Rectangle((0, 0), BED_X, BED_Y, fill=False, lw=2, ec="black"))
        # no-scan zone: sensor overhangs the bed edge for X > SCAN_X_MAX
        self.map_ax.add_patch(
            Rectangle(
                (SCAN_X_MAX, 0),
                BED_X - SCAN_X_MAX,
                BED_Y,
                facecolor="0.85",
                edgecolor="gray",
                hatch="//",
            )
        )
        self.map_ax.text(
            (SCAN_X_MAX + BED_X) / 2,
            BED_Y / 2,
            "no scan\n(sensor offset)",
            ha="center",
            va="center",
            fontsize=8,
            color="0.3",
        )
        self.zone_rect = Rectangle(
            (58, 58), 45, 45, fill=True, alpha=0.25, color="red", lw=1.5, ec="red"
        )
        self.map_ax.add_patch(self.zone_rect)
        self.map_ax.set_xlabel("X (mm)")
        self.map_ax.set_ylabel("Y (mm)")
        self.map_ax.set_title("Prusa MK3S+ bed (250 x 210 mm)")
        self.map_fig.tight_layout()

        self.map_canvas = FigureCanvasTkAgg(self.map_fig, master=frame)
        self.map_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.map_canvas.mpl_connect("button_press_event", self._map_press)
        self.map_canvas.mpl_connect("motion_notify_event", self._map_motion)
        self.map_canvas.mpl_connect("button_release_event", self._map_release)
        self.map_canvas.mpl_connect("scroll_event", self._map_scroll)
        self._sel_start: tuple[float, float] | None = None
        self._rubber: Rectangle | None = None


    def _build_controls(self) -> None:
        frame = ttk.Frame(self.root, padding=8)
        frame.pack(fill="x")

        # zone + density
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Zone X:").pack(side="left")
        self.var_x0 = tk.StringVar(value="58")
        self.var_x1 = tk.StringVar(value="103")
        ttk.Entry(row, textvariable=self.var_x0, width=6).pack(side="left", padx=2)
        ttk.Label(row, text="→").pack(side="left")
        ttk.Entry(row, textvariable=self.var_x1, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="Zone Y:").pack(side="left")
        self.var_y0 = tk.StringVar(value="58")
        self.var_y1 = tk.StringVar(value="103")
        ttk.Entry(row, textvariable=self.var_y0, width=6).pack(side="left", padx=2)
        ttk.Label(row, text="→").pack(side="left")
        ttk.Entry(row, textvariable=self.var_y1, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="Pitch:").pack(side="left")
        self.var_pitch = tk.StringVar(value="3.0")
        ttk.Entry(row, textvariable=self.var_pitch, width=6).pack(side="left", padx=2)
        ttk.Label(row, text="mm/px").pack(side="left")
        self.lbl_points = ttk.Label(row, text="")
        self.lbl_points.pack(side="left", padx=(12, 0))

        # probe params
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Z clearance:").pack(side="left")
        self.var_zclear = tk.StringVar(value="8.0")
        ttk.Entry(row, textvariable=self.var_zclear, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="Step:").pack(side="left")
        self.var_step = tk.StringVar(value="0.1")
        ttk.Entry(row, textvariable=self.var_step, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="Max depth:").pack(side="left")
        self.var_maxdepth = tk.StringVar(value="8.0")
        ttk.Entry(row, textvariable=self.var_maxdepth, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(row, text="mm (≤ Z clearance)").pack(side="left")
        ttk.Label(row, text="   Probe:").pack(side="left")
        self.var_probe = tk.StringVar(value="g30")
        ttk.Combobox(
            row,
            textvariable=self.var_probe,
            values=["g30", "manual"],
            width=7,
            state="readonly",
        ).pack(side="left", padx=2)
        ttk.Label(row, text="(g30 = fast firmware probe)").pack(side="left")

        # plate workaround for warped bed
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Plate lift Z:").pack(side="left")
        self.var_platelift = tk.StringVar(value="50.0")
        ttk.Entry(row, textvariable=self.var_platelift, width=6).pack(side="left", padx=2)
        ttk.Label(
            row,
            text="mm — after G28 W the head lifts here so you can place a flat plate (0 = off)",
        ).pack(side="left", padx=4)

        # connection + actions
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Port:").pack(side="left")
        self.var_port = tk.StringVar(value="/dev/cu.usbmodem21201")
        ttk.Entry(row, textvariable=self.var_port, width=24).pack(side="left", padx=2)
        self.btn_connect = ttk.Button(row, text="Connect", command=self.toggle_connect)
        self.btn_connect.pack(side="left", padx=8)
        self.btn_start = ttk.Button(row, text="Start Scan", command=self.start_scan)
        self.btn_start.pack(side="left")
        self.btn_abort = ttk.Button(row, text="Abort", command=self.abort_scan, state="disabled")
        self.btn_abort.pack(side="left", padx=4)
        self.btn_continue = ttk.Button(row, text="Continue", command=self.resume_scan, state="disabled")
        self.btn_continue.pack(side="left", padx=4)
        self.btn_save = ttk.Button(row, text="Save PNG", command=self.save_png, state="disabled")
        self.btn_save.pack(side="left", padx=4)
        self.btn_clear_map = ttk.Button(row, text="Clear map", command=self.clear_map)
        self.btn_clear_map.pack(side="left", padx=4)
        self.btn_reset_zoom = ttk.Button(row, text="Reset Zoom", command=self.reset_zoom)
        self.btn_reset_zoom.pack(side="left", padx=4)

        # progress

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(6, 0))
        self.progress = ttk.Progressbar(row, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.lbl_progress = ttk.Label(row, text="")
        self.lbl_progress.pack(side="left", padx=(8, 0))

        for var in (self.var_x0, self.var_x1, self.var_y0, self.var_y1, self.var_pitch):
            var.trace_add("write", lambda *_: self._update_summary())
        self._update_summary()

    def _build_image(self) -> None:
        frame = ttk.LabelFrame(self.root, text="Surface height map (live)")
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.img_fig = Figure(figsize=(6.8, 4.0), dpi=100)
        self.img_ax = self.img_fig.add_subplot(111)
        self.im = self.img_ax.imshow(
            np.full((4, 4), np.nan),
            extent=(58, 103, 58, 103),
            origin="lower",
            cmap=spiepy.NANOMAP,
        )
        self.cbar = self.img_fig.colorbar(self.im, ax=self.img_ax)
        self.cbar.set_label("Z (mm)")
        self.img_ax.set_xlabel("X (mm)")
        self.img_ax.set_ylabel("Y (mm)")
        self.img_ax.set_title("no data yet")
        self.img_fig.tight_layout()

        self.img_canvas = FigureCanvasTkAgg(self.img_fig, master=frame)
        self.img_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_statusbar(self) -> None:
        self.lbl_status = ttk.Label(
            self.root, text="Ready.", anchor="w", relief="sunken", padding=(6, 3)
        )
        self.lbl_status.pack(fill="x", side="bottom")

    # ------------------------------------------------------------------
    # bed map interaction
    # ------------------------------------------------------------------

    def _map_press(self, ev) -> None:
        if ev.inaxes is self.map_ax and ev.button == 1:
            self._sel_start = (ev.xdata, ev.ydata)
            if self._rubber is not None:
                self._rubber.remove()
            self._rubber = Rectangle(
                (ev.xdata, ev.ydata), 0, 0, fill=False, lw=1, ls="--", ec="black"
            )
            self.map_ax.add_patch(self._rubber)
            self.map_canvas.draw_idle()

    def _map_motion(self, ev) -> None:
        if self._sel_start is None or self._rubber is None or ev.inaxes is not self.map_ax:
            return
        x0, y0 = self._sel_start
        self._rubber.set_width(ev.xdata - x0)
        self._rubber.set_height(ev.ydata - y0)
        self.map_canvas.draw_idle()

    def _map_release(self, ev) -> None:
        if self._sel_start is None or ev.inaxes is not self.map_ax:
            return
        x0, y0 = self._sel_start
        self._sel_start = None
        xa, xb = sorted((x0, ev.xdata))
        ya, yb = sorted((y0, ev.ydata))
        xa = min(max(xa, MARGIN), SCAN_X_MAX)
        xb = min(max(xb, MARGIN), SCAN_X_MAX)
        ya = min(max(ya, MARGIN), BED_Y - MARGIN)
        yb = min(max(yb, MARGIN), BED_Y - MARGIN)
        if xb - xa < 1 or yb - ya < 1:  # ignore tiny drags/clicks
            if self._rubber is not None:
                self._rubber.remove()
                self._rubber = None
            self.map_canvas.draw_idle()
            return
        self._set_zone(xa, xb, ya, yb)

    def _map_scroll(self, event) -> None:
        if event.inaxes is not self.map_ax:
            return
        
        # Zoom factor
        scale = 0.8 if event.button == 'up' else 1.25
        
        xdata, ydata = event.xdata, event.ydata
        cur_xlim = self.map_ax.get_xlim()
        cur_ylim = self.map_ax.get_ylim()
        
        # Calculate new limits centered around the mouse position
        width = (cur_xlim[1] - cur_xlim[0]) * scale
        height = (cur_ylim[1] - cur_ylim[0]) * scale
        
        new_xlim = [xdata - width/2, xdata + width/2]
        new_ylim = [ydata - height/2, ydata + height/2]
        
        # Clip to bed boundaries
        new_xlim[0] = max(0, new_xlim[0])
        new_xlim[1] = min(BED_X, new_xlim[1])
        new_ylim[0] = max(0, new_ylim[0])
        new_ylim[1] = min(BED_Y, new_ylim[1])
        
        self.map_ax.set_xlim(new_xlim)
        self.map_ax.set_ylim(new_ylim)
        self.map_canvas.draw_idle()

    def _set_zone(self, xa: float, xb: float, ya: float, yb: float) -> None:

        self.var_x0.set(f"{xa:g}")
        self.var_x1.set(f"{xb:g}")
        self.var_y0.set(f"{ya:g}")
        self.var_y1.set(f"{yb:g}")
        self.zone_rect.set_xy((xa, ya))
        self.zone_rect.set_width(xb - xa)
        self.zone_rect.set_height(yb - ya)
        self.map_canvas.draw_idle()
        self._update_summary()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _fval(self, var: tk.StringVar, name: str) -> float:
        try:
            return float(var.get())
        except ValueError:
            raise ValueError(f"invalid number for {name}: {var.get()!r}")

    def _update_summary(self) -> None:
        try:
            cfg = self._read_config()
            self.lbl_points.config(text=f"→ {cfg.nx} x {cfg.ny} = {cfg.total} points")
            # keep the bed-map rectangle in sync with manual entry
            self.zone_rect.set_xy((cfg.x0, cfg.y0))
            self.zone_rect.set_width(cfg.x1 - cfg.x0)
            self.zone_rect.set_height(cfg.y1 - cfg.y0)
            self.map_canvas.draw_idle()
        except ValueError:
            self.lbl_points.config(text="→ (check inputs)")

    def _read_config(self) -> ScanConfig:
        cfg = ScanConfig(
            x0=self._fval(self.var_x0, "Zone X0"),
            x1=self._fval(self.var_x1, "Zone X1"),
            y0=self._fval(self.var_y0, "Zone Y0"),
            y1=self._fval(self.var_y1, "Zone Y1"),
            pitch=self._fval(self.var_pitch, "Pitch"),
            z_clearance=self._fval(self.var_zclear, "Z clearance"),
            step=self._fval(self.var_step, "Step"),
            max_depth=self._fval(self.var_maxdepth, "Max depth"),
            plate_lift=self._fval(self.var_platelift, "Plate lift Z"),
            probe_mode=self.var_probe.get(),
            demo=self.demo,
        )
        if cfg.probe_mode not in ("g30", "manual"):
            raise ValueError("probe mode must be 'g30' or 'manual'")
        if cfg.plate_lift < 0:
            raise ValueError("plate lift Z must be ≥ 0")
        if not (0 <= cfg.x0 < cfg.x1 <= SCAN_X_MAX):
            raise ValueError(
                f"zone X must be within 0..{SCAN_X_MAX:g} (sensor offset) and x0 < x1"
            )
        if not (0 <= cfg.y0 < cfg.y1 <= BED_Y):
            raise ValueError(f"zone Y must be within 0..{BED_Y:g} and y0 < y1")
        if cfg.pitch <= 0:
            raise ValueError("pitch must be > 0")
        if cfg.step <= 0:
            raise ValueError("step must be > 0")
        if cfg.z_clearance <= 0:
            raise ValueError("Z clearance must be > 0")
        if cfg.max_depth > cfg.z_clearance:
            raise ValueError("max depth must be ≤ Z clearance (bed would be hit)")
        return cfg

    def _status(self, msg: str) -> None:
        self.lbl_status.config(text=msg)
        print(msg)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def toggle_connect(self) -> None:
        if self.scanner is not None and self.scanner.is_open:
            self.scanner.close()
            self.scanner = None
            self.homed = False
            self.btn_connect.config(text="Connect")
            self._status("Disconnected.")
            return
        port = self.var_port.get().strip()
        if not port:
            messagebox.showerror("Error", "Enter a serial port.")
            return
        self.btn_connect.config(state="disabled")
        self._status(f"Connecting to {port}...")

        def worker():
            try:
                s = PrusaScanner(port)
                s.connect()
                self.q.put(("connected", s))
            except Exception as e:  # noqa: BLE001
                self.q.put(("connect_error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def start_scan(self) -> None:
        if self.scanner is None or not self.scanner.is_open:
            messagebox.showerror("Error", "Connect to the printer first.")
            return
        try:
            cfg = self._read_config()
        except ValueError as e:
            messagebox.showerror("Invalid settings", str(e))
            return
        msg = (
            f"Scan {cfg.nx} x {cfg.ny} = {cfg.total} points\n"
            f"Zone X {cfg.x0:g}→{cfg.x1:g}, Y {cfg.y0:g}→{cfg.y1:g}, "
            f"pitch {cfg.pitch:g} mm\n\n"
        )
        if not self.homed:
            msg += "The printer will home first (G28 W, no bed leveling). "
        if cfg.plate_lift > 0:
            msg += (
                f"It will then lift to Z {cfg.plate_lift:g} mm and WAIT — "
                "place your flat plate and click Continue."
            )
        else:
            msg += "Is the bed clear except for the object to scan?"
        if not messagebox.askokcancel("Start scan", msg):
            return
        self.abort_event.clear()
        self.continue_event.clear()
        self.image = None
        self.last_csv = None
        self.btn_save.config(state="disabled")
        self.progress["value"] = 0
        self.job = ScanJob(
            self.scanner, cfg, self.q, self.abort_event, self.continue_event,
            do_home=not self.homed,
        )
        if not self.homed:
            self.homed = True  # optimistic; reset on error event
        self.job.start()
        self.btn_start.config(state="disabled")
        self.btn_connect.config(state="disabled")
        self.btn_abort.config(state="normal")
        self.btn_continue.config(state="disabled")
        self._status("Scan started.")

    def abort_scan(self) -> None:
        if self.job is not None:
            self.abort_event.set()
            self._status("Aborting... (finishes current point, then lifts Z)")

    def resume_scan(self) -> None:
        self.continue_event.set()
        self.btn_continue.config(state="disabled")
        self._status("Continuing scan...")

    def save_png(self) -> None:
        if self.image is None:
            return
        default = f"scan_{datetime.now():%Y%m%d_%H%M%S}.png"
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
            initialfile=default,
        )
        if path:
            self.img_fig.savefig(path, dpi=150)
            self._status(f"Saved image: {path}")

    # ------------------------------------------------------------------
    # queue polling (GUI thread)
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                self._handle_event(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_event(self, ev: tuple[Any, ...]) -> None:
        kind = ev[0]

        if kind == "connected":
            self.scanner = ev[1]
            self.homed = False
            self.btn_connect.config(text="Disconnect", state="normal")
            self.btn_start.config(state="normal")
            self._status(f"Connected to {self.scanner.port}.")

        elif kind == "connect_error":
            self.btn_connect.config(text="Connect", state="normal")
            self._status(f"Connection failed: {ev[1]}")
            messagebox.showerror("Connection failed", ev[1])

        elif kind == "status":
            self._status(ev[1])

        elif kind == "grid":
            _, nx, ny, x0, x1, y0, y1 = ev
            self.last_grid = (nx, ny, x0, x1, y0, y1)
            self.image = np.full((ny, nx), np.nan)
            self._prefill_from_previous()
            self.progress["maximum"] = nx * ny
            self.im.set_extent((x0, x1, y0, y1))
            self.img_ax.set_xlim(x0 - 1, x1 + 1)
            self.img_ax.set_ylim(y0 - 1, y1 + 1)
            self.img_ax.set_title(f"scanning {nx} x {ny}")
            self.scan_start = time.monotonic()
            self._refresh_image()

        elif kind == "point":
            _, ix, iy, x, y, z = ev
            if self.image is not None:
                self.image[iy, ix] = np.nan if z is None else z
                self.progress["value"] = iy * self.image.shape[1] + ix + 1
                elapsed = time.monotonic() - self.scan_start
                done = int(self.progress["value"])
                total = int(self.progress["maximum"])
                eta = elapsed / done * (total - done) if done else 0
                self.lbl_progress.config(text=f"{done}/{total}  ETA {eta:.0f}s")
                self._refresh_image()

        elif kind == "pause":
            self.btn_continue.config(state="normal")
            self._status(ev[1] + "  (place flat plate, then click Continue)")

        elif kind in ("done", "aborted"):
            self.job = None
            self.last_csv = ev[1]
            self.btn_start.config(state="normal")
            self.btn_connect.config(state="normal")
            self.btn_abort.config(state="disabled")
            self.btn_continue.config(state="disabled")
            self.btn_save.config(state="normal")
            self.img_ax.set_title("complete" if kind == "done" else "aborted")
            self._refresh_image()
            self._archive_scan_on_map()
            where = f" CSV: {ev[1]}" if ev[1] else ""
            self._status(f"Scan {kind}.{where}")

        elif kind == "error":
            self.job = None
            self.homed = False  # position unknown after a failure: re-home next scan
            self.btn_start.config(state="normal")
            self.btn_connect.config(state="normal")
            self.btn_abort.config(state="disabled")
            self.btn_continue.config(state="disabled")
            self._status(f"Error: {ev[1]}")
            messagebox.showerror("Scan error", ev[1])

    # ------------------------------------------------------------------
    # scan archiving / bed-map overlay
    # ------------------------------------------------------------------

    @staticmethod
    def _grid_axes(n: int, a: float, b: float) -> np.ndarray:
        return np.linspace(a, b, n) if n > 1 else np.array([a])

    def _prefill_from_previous(self) -> None:
        """Fill NaN cells of the new scan matrix with the nearest value from
        previously completed scans (newest first), so a fine scan starts from
        the underlying coarse data."""
        if self.image is None or self.last_grid is None or not self.completed_scans:
            return
        nx, ny, x0, x1, y0, y1 = self.last_grid
        xs = self._grid_axes(nx, x0, x1)
        ys = self._grid_axes(ny, y0, y1)
        for scan in reversed(self.completed_scans):
            sx, sy, sz = scan["xs"], scan["ys"], scan["z"]
            if sx.size < 2 or sy.size < 2:
                continue
            ix = np.round((xs - sx[0]) / (sx[1] - sx[0])).astype(int)
            iy = np.round((ys - sy[0]) / (sy[1] - sy[0])).astype(int)
            IX, IY = np.meshgrid(ix, iy)  # shapes (ny, nx)
            valid = (IX >= 0) & (IX < sx.size) & (IY >= 0) & (IY < sy.size)
            mask = valid & np.isnan(self.image)
            if not mask.any():
                continue
            vals = sz[IY[mask], IX[mask]]
            self.image[mask] = np.where(np.isfinite(vals), vals, np.nan)

    def _archive_scan_on_map(self) -> None:
        """Store the just-finished scan and draw it on the bed map."""
        if self.image is None or self.last_grid is None:
            return
        nx, ny, x0, x1, y0, y1 = self.last_grid
        if np.isnan(self.image).all():
            return
        scan = {
            "xs": self._grid_axes(nx, x0, x1),
            "ys": self._grid_axes(ny, y0, y1),
            "z": self.image.copy(),
        }
        self.completed_scans.append(scan)
        im = self.map_ax.imshow(
            scan["z"],
            extent=(x0, x1, y0, y1),
            origin="lower",
            cmap=spiepy.NANOMAP,
            interpolation="nearest",
        )
        self.map_images.append(im)
        if self.map_cbar is None:
            self.map_cbar = self.map_fig.colorbar(
                im, ax=self.map_ax, label="Z (mm)", pad=0.02
            )
            self.map_fig.tight_layout()
        else:
            # rescale to the combined range of all archived scans; keep the
            # colorbar bound to the first image (update_normal would rebind it)
            allz = np.concatenate([s["z"].ravel() for s in self.completed_scans])
            lo, hi = np.nanmin(allz), np.nanmax(allz)
            if hi > lo:
                first = self.map_images[0]
                first.set_clim(lo, hi)
                im.set_clim(lo, hi)
                self.map_cbar.update_normal(first)
        self.map_canvas.draw_idle()

    def clear_map(self) -> None:
        if self.map_cbar is not None:
            self.map_cbar.remove()  # must go before its mappable image
            self.map_cbar = None
        for im in self.map_images:
            im.remove()
        self.map_images = []
        self.completed_scans = []
        self.map_fig.tight_layout()
        self.map_canvas.draw_idle()
        self._status("Bed map cleared.")

    def reset_zoom(self) -> None:
        """Reset the bed map zoom to show the full bed."""
        self.map_ax.set_xlim(0, BED_X)
        self.map_ax.set_ylim(0, BED_Y)
        self.map_canvas.draw_idle()
        self._status("Zoom reset.")

    def _refresh_image(self) -> None:
        if self.image is None:
            return
        self.im.set_array(self.image)
        # live-rescale the colorbar to the data seen so far
        if not np.isnan(self.image).all():
            lo, hi = np.nanmin(self.image), np.nanmax(self.image)
            if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                self.im.set_clim(lo, hi)
        self.img_canvas.draw_idle()


def main(demo: bool = False) -> None:
    root = tk.Tk()
    App(root, demo=demo)
    root.mainloop()
