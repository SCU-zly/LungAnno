"""Arq worker queue setup."""
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings


def _redis_settings_from_url(url: str) -> RedisSettings:
    """Parse redis://[:password@]host[:port][/db] into arq RedisSettings.

    Single source of truth is settings.redis_url (REDIS_URL env) — previously
    the URL was hardcoded in both queue.py and run_worker.py while the
    settings value was never referenced.
    """
    u = urlparse(url)
    return RedisSettings(
        host=u.hostname or "localhost",
        port=u.port or 6379,
        database=int(u.path.lstrip("/") or 0),
        password=u.password or None,
    )


redis_settings = _redis_settings_from_url(settings.redis_url)

# Module-level singleton pool. Creating a new pool per enqueue and never
# closing it leaks one pool (several connections) per series ingested.
_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings)
    return _pool


async def enqueue_preprocess(series_id: int):
    """Enqueue a preprocessing job for the given series."""
    redis = await _get_pool()
    await redis.enqueue_job("preprocess_series", series_id)


async def enqueue_inference(series_id: int):
    """Enqueue an inference job for the given series."""
    redis = await _get_pool()
    await redis.enqueue_job("run_inference", series_id)
