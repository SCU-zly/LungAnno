"""Series model."""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class Series(Base):
    __tablename__ = "series"

    id = Column(Integer, primary_key=True, index=True)
    series_uid = Column(String(128), unique=True, nullable=False, index=True)
    study_id = Column(Integer, ForeignKey("studies.id"), nullable=False)
    processing_status = Column(String(32), default="registered", nullable=False)
    review_status = Column(String(32), default="not_reviewed", nullable=False)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    slice_thickness = Column(Float, nullable=True)  # DICOM SliceThickness（mm），缺失/为 0 记 NULL
    series_description = Column(String(128), nullable=True)  # DICOM SeriesDescription
    raw_path = Column(String(512), nullable=True)
    preprocessed_path = Column(String(512), nullable=True)
    meta_path = Column(String(512), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
