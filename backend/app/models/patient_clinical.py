"""Patient clinical metadata model - 临床表（CSV 导入）按住院号关联 studies。"""
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class PatientClinical(Base):
    __tablename__ = "patient_clinical"

    id = Column(Integer, primary_key=True, index=True)
    # 规范化住院号（去前导零），与 studies.patient_id 经 ltrim(...,'0') 关联
    patient_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(64), nullable=True)  # 患者姓名（中文）
    payload = Column(JSONB, nullable=True)  # 临床行全部非空列（值已转 str）
