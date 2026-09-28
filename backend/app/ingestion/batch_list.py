"""Batches list API endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.database import get_db
from app.auth.dependencies import get_current_user
from app.models.batch import Batch
from app.models.series import Series
from app.models.batch_study import batch_studies

# Extend ingestion router with batch list endpoint
# This is imported in main.py via ingestion router
