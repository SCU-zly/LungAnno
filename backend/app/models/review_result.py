"""Review result model."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class ReviewResult(Base):
    __tablename__ = "review_results"

    id = Column(Integer, primary_key=True, index=True)
    detection_id = Column(Integer, ForeignKey("detections.id"), nullable=False, index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    verdict = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
