"""Pipeline orchestrator."""
from app.database import SessionLocal
from app.models.series import Series


def get_pipeline_status(series_id: int) -> dict:
    db = SessionLocal()
    try:
        series = db.query(Series).filter(Series.id == series_id).first()
        if not series:
            return None
        return {
            "series_id": series.id,
            "series_uid": series.series_uid,
            "processing_status": series.processing_status,
            "review_status": series.review_status,
        }
    finally:
        db.close()
