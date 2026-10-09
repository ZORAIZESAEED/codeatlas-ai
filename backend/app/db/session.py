from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///") and not database_url.endswith(":memory:"):
        sqlite_path = database_url.removeprefix("sqlite:///")
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
