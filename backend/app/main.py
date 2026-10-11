import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.llm import router as llm_router
from app.api.projects import ingestion_error_handler
from app.api.projects import router as projects_router
from app.api.search import router as search_router
from app.core.config import Settings, get_settings
from app.db.session import create_db_engine
from app.services.conversations import recover_interrupted_messages
from app.services.indexing.indexer import recover_interrupted_runs
from app.services.ingestion import IngestionError

logger = logging.getLogger(__name__)


def _recover_indexing(session_factory) -> None:
    """A server stopped mid-indexing leaves the row in ``indexing``; report it as failed."""
    try:
        with session_factory() as session:
            recovered = recover_interrupted_runs(session)
        if recovered:
            logger.warning("Marked %d interrupted indexing run(s) as failed", recovered)
    except OperationalError:
        # Database not migrated to 0004 yet; search endpoints will report the problem.
        logger.warning("Search index tables missing; run `alembic upgrade head`")
    try:
        with session_factory() as session:
            recovered = recover_interrupted_messages(session)
        if recovered:
            logger.warning("Marked %d interrupted chat answer(s) as failed", recovered)
    except OperationalError:
        # Database not migrated to 0005 yet; chat endpoints will report the problem.
        logger.warning("Chat tables missing; run `alembic upgrade head`")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_db_engine(config.database_url)
        app.state.engine = engine
        app.state.settings = config
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        # Run `alembic upgrade head` before starting the server.
        _recover_indexing(app.state.session_factory)
        yield
        engine.dispose()

    app = FastAPI(title=config.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(search_router)
    app.include_router(llm_router)
    app.include_router(chat_router)
    app.add_exception_handler(IngestionError, ingestion_error_handler)
    return app


app = create_app()
