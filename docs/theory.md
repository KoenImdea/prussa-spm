# Theory of Operation

## The SPM Principle

A Scanning Probe Microscope (SPM) works by scanning a physical probe over a surface and measuring the vertical displacement of the probe to determine the topography. 

In this demo, we use a **contact-mode** approach. The probe is forced against the surface, and the vertical position is recorded at the point of contact.

## Hardware Implementation

### The Probe
The probe consists of a cantilever beam with a tip. In this implementation, the cantilever is mounted to the Prusa print head. Instead of a complex piezoelectric sensor, we use the **PINDA inductive sensor**. 

When the cantilever touches the sample and bends upwards, it moves the metal of the cantilever (or a separate trigger plate) into the sensing range of the PINDA sensor, triggering a digital signal.

### Motion Control
The Prusa MK3S+ provides high-precision stepper motor control. We use G-code to command the head to move in a grid pattern. 

To avoid dragging the probe across the sample and potentially damaging it or the surface, we use a **decoupled travel** strategy:
1. Lift Z to a safe clearance height.
2. Move to the next $(X, Y)$ coordinate.
3. Descend until contact is detected.

## Probing Methods

### Firmware-based (`G30`)
The `G30` command tells the Prusa firmware to move the Z-axis down until the probe triggers. This is highly efficient because the trigger detection happens in the firmware's real-time loop, minimizing the distance travelled after the trigger event.

### Software-based (`manual`)
In manual mode, the software sends a series of small relative Z-moves (e.g., $-0.1\text{mm}$) and queries the probe status (`M119`) after each move. This provides more control over the descent speed and step size, though it is slower and has a resolution limited by the step size.

## Data Processing & Visualization
The resulting data is a set of points $(x, y, z)$. This can be interpolated to create a continuous surface map. Because the Prusa bed is often not perfectly flat, using a reference plate (Plate Lift feature) allows for the subtraction of the bed's curvature, leaving only the topography of the sample.

Height maps are rendered with Matplotlib using the **NANOMAP** colormap (from the [`spiepy`](https://pypi.org/project/spiepy/) package) — a color scheme designed for surface/topography data where adjacent height levels remain easily distinguishable. Points without contact are stored as NaN and rendered as transparent holes, so they never distort the colorbar range.
