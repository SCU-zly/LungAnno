"""Coordinate utilities for detection results."""
import numpy as np


def world_to_voxel(box_world: list, spacing: list, origin: list = None) -> dict:
    """Convert world-coordinate (RAS mm) box to voxel coordinates + slice index."""
    sp = np.array(spacing)
    org = np.array(origin) if origin else np.zeros(3)
    x, y, z, w, h, d = box_world
    vx = int(round((x - org[0]) / sp[0]))
    vy = int(round((y - org[1]) / sp[1]))
    vz = int(round((z - org[2]) / sp[2]))
    vw = max(1, int(round(w / sp[0])))
    vh = max(1, int(round(h / sp[1])))
    vd = max(1, int(round(d / sp[2])))
    return {"voxel_x": vx, "voxel_y": vy, "voxel_z": vz, "voxel_w": vw, "voxel_h": vh, "voxel_d": vd, "slice_index": vz}
