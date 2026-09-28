"""Batch-User access grant model - 批次可见性授权（reviewer 仅见授权批次）。"""
from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.database import Base


class BatchUserAccess(Base):
    __tablename__ = "batch_user_access"

    batch_id = Column(Integer, ForeignKey("batches.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
