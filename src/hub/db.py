from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from hub.config import get_settings


@lru_cache
def engine_for(role: str) -> Engine:
    settings = get_settings()
    connect_args: dict = {}
    if settings.database_ssl_ca:
        connect_args["ssl"] = {"ca": settings.database_ssl_ca, "check_hostname": True}
    engine = create_engine(settings.url_for(role), pool_pre_ping=True, future=True, connect_args=connect_args)

    @event.listens_for(engine, "connect")
    def _utc(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET time_zone = '+00:00'")
        cursor.close()

    return engine


def session_for(role: str) -> Session:
    return sessionmaker(bind=engine_for(role), expire_on_commit=False, future=True)()


def session_scope(role: str) -> Iterator[Session]:
    session = session_for(role)
    try:
        yield session
    finally:
        session.close()
