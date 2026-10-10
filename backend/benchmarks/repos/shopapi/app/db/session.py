from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

_engine = None
SessionLocal = sessionmaker(autoflush=False)


def init_engine():
    """Create the SQLAlchemy engine and connection pool and bind the session factory."""
    global _engine
    settings = get_settings()
    _engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )
    SessionLocal.configure(bind=_engine)
    return _engine


def get_session():
    """Yield a session per request and always close it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
