import numpy as np, spiepy
import matplotlib; matplotlib.use("Agg")                                                                                             
import matplotlib.pyplot as plt                                                                                                      
from scipy.ndimage import map_coordinates                                                                                            
                                                                                                                                      
H = np.load("demo_surface/heightmap_010.npy").astype(np.float64)                                                                     
H[~np.isfinite(H)] = np.nan                                                                                                          
Hf, _ = spiepy.flatten.xy(np.where(np.isnan(H),0,H), np.isnan(H))                                                                    
                                                                                                                                      
d = np.genfromtxt("scans/scan_20260921_190220.csv", delimiter=",", names=True)                                                       
xs, ys = np.unique(d["x_mm"]), np.unique(d["y_mm"])                                                                                  
Z = np.full((len(ys),len(xs)), np.nan)                                                                                               
Z[np.searchsorted(ys,d["y_mm"]), np.searchsorted(xs,d["x_mm"])] = d["z_mm"]                                                          
Z[~np.isfinite(Z)] = np.nan                                                                                                          
Zf,_ = spiepy.flatten.xy(Z, np.isnan(Z))                                                                                             
                                                                                                                                      
fig,ax=plt.subplots(1,3,figsize=(18,6))                                                                                              
im0=ax[0].pcolormesh(Hf, cmap='viridis', shading='auto')                                                                             
ax[0].set_title("heightmap (flattened), axis0=x axis1=y")                                                                            
ax[0].set_aspect('equal')  
P=0.1
# heightmap convention: pixel (i,j) center at x = 225 - (i+0.5)*P, y = 195 - (j+0.5)*P
X0=np.array([225.,195.])
bx=np.linspace(100,202,52); by=np.linspace(9,125,59)                                                                                 
BB=np.stack(np.meshgrid(bx,by),-1).reshape(-1,2)                                                                                     
px=(X0-BB)/P-0.5
reg=map_coordinates(Hf,[px[:,0],px[:,1]],order=1).reshape(len(by),len(bx))                                                           
im1=ax[1].pcolormesh(bx,by,reg,cmap='viridis',shading='auto')                                                                        
ax[1].set_title("heightmap sampled at fine-scan region (c=125,95)")                                                                  
ax[1].set_aspect('equal')                                                                                                            
im2=ax[2].pcolormesh(xs,ys,Zf,cmap='viridis',shading='auto')                                                                         
ax[2].set_title("fine scan (flattened)")                                                                                             
ax[2].set_aspect('equal')                                                                                                            
for a in ax: a.set_xlabel('x mm'); a.set_ylabel('y mm')                                                                              
fig.colorbar(im0,ax=ax[0]); fig.colorbar(im1,ax=ax[1]); fig.colorbar(im2,ax=ax[2])                                                   
fig.tight_layout(); fig.savefig("debug_compare-man.png",dpi=120)                                                                         
print("saved debug_compare.png")                                                                                                     
print("heightmap region z range:", np.nanmin(reg), np.nanmax(reg))                                                                   
print("fine scan z range:", np.nanmin(Zf), np.nanmax(Zf))   