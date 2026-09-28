"""Arq worker runner script."""
from app.config import settings
from app.worker.queue import redis_settings as _redis_settings
from app.worker.preprocess import preprocess_series
from app.worker.inference import run_inference


class WorkerSettings:
    functions = [preprocess_series, run_inference]
    redis_settings = _redis_settings
    max_jobs = settings.arq_max_jobs
    job_timeout = settings.arq_job_timeout
