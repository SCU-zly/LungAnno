"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.auth.router import router as auth_router
from app.ingestion.router import router as ingestion_router
from app.series.router import router as series_router
from app.review.router import router as review_router
from app.admin.router import router as admin_router

logger = logging.getLogger(__name__)

# 公开已知的占位默认值 —— 生产部署 MUST 通过环境变量覆盖（ISS-001/ISS-006）
INSECURE_DEFAULTS = {
    "jwt_secret": "change-me-in-production-use-secrets-manager",
    "database_url": "postgresql://ctanno:ctanno@localhost:5432/ctanno",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for name, insecure_value in INSECURE_DEFAULTS.items():
        if getattr(settings, name) == insecure_value:
            logger.warning(
                "INSECURE DEFAULT DETECTED: %s is using its known placeholder value. "
                "Set the %s environment variable (or .env) before any production deployment.",
                name,
                name.upper(),
            )
    yield


app = FastAPI(
    title="CT Image Intelligent Annotation System",
    version="0.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(ingestion_router)
app.include_router(series_router)
app.include_router(review_router)
app.include_router(admin_router)


@app.get("/health")
def health_check():
    """Liveness probe: verify DB connectivity with a real query (REV-001)."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        return {"status": "degraded", "database": "unreachable"}
    finally:
        db.close()


@app.get("/health/ready")
def readiness_check():
    return {"status": "ready"}
