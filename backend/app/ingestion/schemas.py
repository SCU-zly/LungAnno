"""Ingestion Pydantic schemas."""
from pydantic import BaseModel


class IngestRequest(BaseModel):
    directory_path: str
    batch_name: str


class IngestResponse(BaseModel):
    batch_id: int
    batch_name: str
    study_count: int
    series_count: int
    dicom_count: int
