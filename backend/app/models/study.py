"""Study model."""
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Study(Base):
    __tablename__ = "studies"

    id = Column(Integer, primary_key=True, index=True)
    study_uid = Column(String(128), unique=True, nullable=False, index=True)
    patient_id = Column(String(128), nullable=True)
    patient_name = Column(String(128), nullable=True)  # DICOM PatientName 原样（多为拼音，中文名兜底来源）
    study_date = Column(String(8), nullable=True)  # DICOM StudyDate（YYYYMMDD 字符串）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
