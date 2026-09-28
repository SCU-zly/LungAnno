"""Review Pydantic schemas."""
from pydantic import BaseModel
from typing import List, Optional


class DetectionVerdict(BaseModel):
    detection_id: int
    verdict: str  # "accepted" | "rejected" | "uncertain"


class SubmitReviewRequest(BaseModel):
    verdicts: List[DetectionVerdict]


class ReviewStatusResponse(BaseModel):
    series_id: int
    review_status: str
    reviewer_id: Optional[int] = None
    lease_expires_at: Optional[str] = None
