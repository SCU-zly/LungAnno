"""Batch-Study association table."""
from sqlalchemy import Table, Column, Integer, ForeignKey
from app.database import Base

batch_studies = Table(
    "batch_studies",
    Base.metadata,
    Column("batch_id", Integer, ForeignKey("batches.id"), primary_key=True),
    Column("study_id", Integer, ForeignKey("studies.id"), primary_key=True),
)
