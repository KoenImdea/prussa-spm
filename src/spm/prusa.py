"""Serial communication with a Prusa MK3S+ (PrusaLink G-code subset).

Only commands supported by Prusa firmware are used:
G28, G90/G91, G0/G1, M119, M400, M112.
"""

from __future__ import annotations

import re
import time

import serial


class PrusaError(Exception):
    """Raised on serial / protocol errors."""


class PrusaScanner:
    """
    Handles serial communication with a Prusa MK3S+ printer to perform scanning.

    This class implements a subset of G-code commands to control the printer
    and query the PINDA probe sensor for contact detection.

    Attributes
    ----------
    port : str
        The serial port to connect to (e.g., '/dev/cu.usbmodem...').
    baud : int
        Baud rate for serial communication. Defaults to 115200.
    read_timeout : float
        Timeout for reading from the serial port in seconds. Defaults to 2.0.
    ser : serial.Serial or None
        The active serial connection object.
    """
    def __init__(self, port: str, baud: int = 115200, read_timeout: float = 2.0):
        self.port = port
        self.baud = baud
        self.read_timeout = read_timeout
        self.ser: serial.Serial | None = None

    # -- connection -----------------------------------------------------

    @property
    def is_open(self) -> bool:
        """
        Check if the serial connection is open.

        Returns
        -------
        bool
            True if connected and open, False otherwise.
        """
        return self.ser is not None and self.ser.is_open

    def connect(self) -> None:
        """
        Establishes a serial connection to the printer.

        Waits for a short period to allow for bootloader/reboot handshakes.
        """
        if self.is_open:
            return
        self.ser = serial.Serial(
            self.port, self.baud, timeout=self.read_timeout, dsrdtr=True
        )
        time.sleep(2)  # wait for bootloader/reboot handshake
        self.ser.reset_input_buffer()

    def close(self) -> None:
        """
        Closes the serial connection and releases the resource.
        """
        if self.is_open:
            self.ser.close()
        self.ser = None

    # -- protocol -------------------------------------------------------

    def send_command(self, cmd: str, timeout_s: float = 120.0) -> list[str]:
        """
        Send a G-code command and wait for the 'ok' response from the printer.

        Parameters
        ----------
        cmd : str
            The G-code command to send.
        timeout_s : float, optional
            Maximum time to wait for the 'ok' response in seconds. Defaults to 120.0.

        Returns
        -------
        list of str
            A list of all response lines received from the printer.

        Raises
        ------
        PrusaError
            If the printer is not connected or if the 'ok' response times out.
        """
        if not self.is_open:
            raise PrusaError("not connected")
        assert self.ser is not None
        self.ser.write(f"{cmd}\n".encode("utf-8"))

        lines: list[str] = []
        deadline = time.monotonic() + timeout_s
        while True:
            if time.monotonic() > deadline:
                raise PrusaError(f"timeout waiting for 'ok' after: {cmd}")
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
            if line.lower().startswith("ok"):
                return lines

    def is_pinda_triggered(self) -> bool:
        """
        Query the printer for endstop statuses (M119) and check if PINDA is triggered.

        Returns
        -------
        bool
            True if the Z-probe (PINDA) is triggered, False otherwise.
        """
        lines = self.send_command("M119")
        for line in lines:
            low = line.lower()
            if ("z_probe" in low or "z_min" in low) and "triggered" in low:
                # "NOT_TRIGGERED" also contains "triggered"
                if "not_triggered" not in low:
                    return True
        return False

    # -- motion ---------------------------------------------------------

    def home(self) -> None:
        """
        Home the Z-axis only (G28 W).
        """
        self.send_command("G28 W", timeout_s=300)

    def raise_z(self, z: float, feed: int = 6000) -> None:
        """
        Raise the Z-axis to an absolute height.

        Parameters
        ----------
        z : float
            The target Z height in mm.
        feed : int, optional
            Feed rate in mm/min. Defaults to 6000.
        """
        self.send_command("G90")
        self.send_command(f"G01 Z{z:g} F{feed}")
        self.send_command("M400")

    def travel_to(self, x: float, y: float, z_clearance: float, feed: int = 6000) -> None:
        """
        Move to (x, y) at clearance height with Z and XY decoupled.

        The movement is split into two steps:
        1. Lift Z to clearance (Z-only move).
        2. Move XY (horizontal-only move).
        This avoids diagonal moves where the tip can run into the surface
        between points of different height.

        Parameters
        ----------
        x : float
            Target X coordinate in mm.
        y : float
            Target Y coordinate in mm.
        z_clearance : float
            Z height to maintain during XY travel in mm.
        feed : int, optional
            Feed rate in mm/min. Defaults to 6000.
        """
        self.send_command("G90")
        self.send_command(f"G01 Z{z_clearance:g} F{feed}")
        self.send_command("M400")
        self.send_command(f"G01 X{x:g} Y{y:g} F{feed}")
        self.send_command("M400")

    def probe_point(
        self,
        x: float,
        y: float,
        z_clearance: float,
        step: float = 0.1,
        max_depth: float = 8.0,
        feed: int = 1500,
    ) -> float | None:
        """
        Travel to (x, y) at clearance height, then step down until the probe triggers.

        Parameters
        ----------
        x : float
            Target X coordinate in mm.
        y : float
            Target Y coordinate in mm.
        z_clearance : float
            Z height to start the probe from in mm.
        step : float, optional
            Z-step size during descent in mm. Defaults to 0.1.
        max_depth : float, optional
            Maximum Z descent distance before giving up in mm. Defaults to 8.0.
        feed : int, optional
            Feed rate during descent in mm/min. Defaults to 1500.

        Returns
        -------
        float or None
            The Z height at contact in mm, or None if no contact within max_depth.
            Leaves the nozzle at the contact height (caller must raise Z).
        """
        self.travel_to(x, y, z_clearance)

        self.send_command("G91")  # relative descent
        descended = 0.0
        max_steps = int(max_depth / step)
        for _ in range(max_steps):
            if self.is_pinda_triggered():
                return z_clearance - descended
            self.send_command(f"G01 Z-{step:g} F{feed}")
            descended += step
        return None  # safety: exceeded max depth without contact

    def emergency_stop(self) -> None:
        """
        Send M112 emergency stop.
        The firmware halts immediately and the serial connection is lost.
        """
        if self.is_open:
            self.ser.write(b"M112\n")

    # G30 output: "Bed X: 100.00000 Y: 100.00000 Z: 2.50500"
    _BED_RE = re.compile(r"Bed\s+X:\s*([\d.\-]+)\s+Y:\s*([\d.\-]+)\s+Z:\s*([\d.\-]+)")

    def probe_g30(
        self, x: float, y: float, z_clearance: float, no_contact_below: float = -2.0
    ) -> float | None:
        """
        Probe at (x, y) using the firmware's G30 command.

        This is faster than `probe_point` as it uses a single command and the
        firmware controls the descent.

        Parameters
        ----------
        x : float
            Target X coordinate in mm.
        y : float
            Target Y coordinate in mm.
        z_clearance : float
            Z height to start the probe from in mm.
        no_contact_below : float, optional
            Z value below which the probe is considered not to have triggered.
            Defaults to -2.0.

        Returns
        -------
        float or None
            The trigger Z height in mm, or None if the probe did not trigger.

        Raises
        ------
        PrusaError
            If G30 does not report a Z value in its response.
        """
        self.travel_to(x, y, z_clearance)
        lines = self.send_command("G30")
        for line in lines:
            m = self._BED_RE.search(line)
            if m:
                z = float(m.group(3))
                return None if z < no_contact_below else z
        raise PrusaError(f"G30 did not report a Z value: {lines}")
