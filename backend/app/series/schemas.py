"""Series API schemas."""
from pydantic import BaseModel
from typing import Optional, List


class SeriesStatusResponse(BaseModel):
    series_id: int
    series_uid: str
    processing_status: str
    review_status: str


class SeriesListItem(BaseModel):
    series_id: int
    series_uid: str
    review_status: str
    processing_status: Optional[str] = None
    detection_count: Optional[int] = None


class BatchDetailResponse(BaseModel):
    batch_id: int
    batch_name: str
    series_list: List[SeriesListItem]


class DetectionItem(BaseModel):
    """Detection response: world-coordinate box (mm, center+whd) as source of truth
    plus denormalized voxel coordinates for fast overlay rendering (SA-06)."""

    id: int
    score: float
    slice_index: int
    box_x: float
    box_y: float
    box_z: float
    box_w: float
    box_h: float
    box_d: float
    voxel_x: int
    voxel_y: int
    voxel_z: int
    voxel_w: int
    voxel_h: int
    voxel_d: int
