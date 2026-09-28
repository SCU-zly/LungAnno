"""Review state machine."""
from datetime import datetime, timedelta, timezone
from app.config import settings

VALID_TRANSITIONS = {
    "not_reviewed": ["in_review"],
    "in_review": ["reviewed"],
    "reviewed": [],
}


def can_transition(current: str, target: str) -> bool:
    return target in VALID_TRANSITIONS.get(current, [])


def is_lease_valid(lease_expires_at: datetime) -> bool:
    if lease_expires_at is None:
        return False
    return datetime.now(timezone.utc) < lease_expires_at


def compute_lease_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=settings.reviewer_lease_minutes)


def release_expired_lease(series) -> bool:
    """Lazily release an expired review lease (REV-002).

    Called on read/claim paths: an in_review series whose lease has expired
    falls back to not_reviewed so the next reviewer can take it over.
    Returns True if the lease was released.
    """
    if series.review_status == "in_review" and not is_lease_valid(series.lease_expires_at):
        series.review_status = "not_reviewed"
        series.reviewer_id = None
        series.lease_expires_at = None
        return True
    return False
