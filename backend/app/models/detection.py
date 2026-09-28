"""Detection model - stores model-predicted nodule candidates."""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Detection(Base):
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True, index=True)
    series_id = Column(Integer, ForeignKey("series.id"), nullable=False, index=True)
    score = Column(Float, nullable=False)
    # World-coordinate box (RAS) - source of truth
    box_x = Column(Float, nullable=False)
    box_y = Column(Float, nullable=False)
    box_z = Column(Float, nullable=False)
    box_w = Column(Float, nullable=False)
    box_h = Column(Float, nullable=False)
    box_d = Column(Float, nullable=False)
    # Denormalized voxel coordinates - for fast overlay rendering
    voxel_x = Column(Integer, nullable=False)
    voxel_y = Column(Integer, nullable=False)
    voxel_z = Column(Integer, nullable=False)
    voxel_w = Column(Integer, nullable=False)
    voxel_h = Column(Integer, nullable=False)
    voxel_d = Column(Integer, nullable=False)
    slice_index = Column(Integer, nullable=False)
    label = Column(String(64), default="nodule")
    # 候选来源模型变体：dlcsd | monai_bundle | deeplung（老数据默认 dlcsd）
    source = Column(String(32), server_default="dlcsd", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
