import numpy as np, spiepy
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.ndimage import map_coordinates

heightmap_dimension_px = [2000, 2000] # exported from build123d
heightmap_dimension_mm = [200, 200]  # printed by bambu

plate_position_mm = [120.228, 96.999] # x, y, in mm, on prussa board; very approx

# Convention: H (and Hf) have shape (ny, nx); axis0 = y (rows), axis1 = x (cols).
# Row 0 corresponds to y = plate_y_lim[0], col 0 to x = plate_x_lim[0].
# The plate is assumed axis-aligned on the bed (no rotation; angles ignored for now).

plate_x_lim = (plate_position_mm[0] - heightmap_dimension_mm[0]/2, plate_position_mm[0] + heightmap_dimension_mm[0]/2)
plate_y_lim = (plate_position_mm[1] - heightmap_dimension_mm[1]/2, plate_position_mm[1] + heightmap_dimension_mm[1]/2)

# --- 1. Load and flatten the coarse heightmap ---
# Load heightmap from .npy file
H = np.load("demo_surface/heightmap_010.npy").astype(np.float64)
H[~np.isfinite(H)] = np.nan

# Flatten the heightmap using spiepy. 
# np.where(np.isnan(H),0,H) ensures NaNs are treated as 0 for the flattening operation, 
# while np.isnan(H) marks the invalid regions.
Hf, _ = spiepy.flatten.xy(np.where(np.isnan(H),0,H), np.isnan(H))
xf = np.linspace(plate_x_lim[0], plate_x_lim[1], heightmap_dimension_px[0])
yf = np.linspace(plate_y_lim[0], plate_y_lim[1], heightmap_dimension_px[1])


# --- 2. Load and flatten the fine scan data ---
# Load scan data from CSV (expected columns: x_mm, y_mm, z_mm)
d = np.genfromtxt("scans/scan_20260921_190220.csv", delimiter=",", names=True)
xs, ys = np.unique(d["x_mm"]), np.unique(d["y_mm"])

# Reconstruct the scan data into a 2D grid
Z = np.full((len(ys),len(xs)), np.nan)
Z[np.searchsorted(ys,d["y_mm"]), np.searchsorted(xs,d["x_mm"])] = d["z_mm"]
Z[~np.isfinite(Z)] = np.nan

# Flatten the fine scan data
Zf,_ = spiepy.flatten.xy(Z, np.isnan(Z))

# --- 3. Visualization and Comparison ---
fig,ax=plt.subplots(1,3,figsize=(18,6))

# Plot 0: The full flattened heightmap
im0=ax[0].pcolormesh(xf, yf, Hf, cmap='viridis', shading='auto')
ax[0].set_title("heightmap (flattened)")
ax[0].set_aspect('equal')

# Plot 1: Sample the heightmap at the exact points of the fine scan
# Map physical coordinates (xs, ys) from CSV back to heightmap pixel indices
X_grid, Y_grid = np.meshgrid(xs, ys)

# Using the formula: index = (val - min) / (max - min) * (N - 1)
idx_x = (X_grid - plate_x_lim[0]) / (plate_x_lim[1] - plate_x_lim[0]) * (heightmap_dimension_px[0] - 1)
idx_y = (Y_grid - plate_y_lim[0]) / (plate_y_lim[1] - plate_y_lim[0]) * (heightmap_dimension_px[1] - 1)

# Sample the flattened heightmap (Hf) at these calculated pixel positions
# map_coordinates expects indices in order of array axes: [axis0, axis1, ...]
# axis0 = y (rows), axis1 = x (cols) -- see convention at top of file
reg = map_coordinates(Hf, [idx_y.ravel(), idx_x.ravel()], order=1).reshape(len(ys), len(xs))

im1=ax[1].pcolormesh(xs, ys, reg, cmap='viridis', shading='auto')
ax[1].set_title("heightmap sampled at fine-scan points")
ax[1].set_aspect('equal')

# Plot 2: The flattened fine scan
im2=ax[2].pcolormesh(xs,ys,Zf,cmap='viridis',shading='auto')
ax[2].set_title("fine scan (flattened)")
ax[2].set_aspect('equal')

# Formatting and saving
for a in ax: a.set_xlabel('x mm'); a.set_ylabel('y mm')
fig.colorbar(im0,ax=ax[0]); fig.colorbar(im1,ax=ax[1]); fig.colorbar(im2,ax=ax[2])
fig.tight_layout(); fig.savefig("debug_compare-man.png",dpi=120)

print("saved debug_compare-man.png")
print("heightmap region z range:", np.nanmin(reg), np.nanmax(reg))
print("fine scan z range:", np.nanmin(Zf), np.nanmax(Zf))
