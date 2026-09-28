"""Database engine and session management."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
