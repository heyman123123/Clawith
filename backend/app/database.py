"""Database connection and session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    # NullPool: open a fresh connection per request so reads never see a
    # stale transaction snapshot from a previously-checked-in connection.
    poolclass=NullPool,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions.

    Rolls back any leftover transaction state from the connection pool
    before yielding, so reads always see the latest committed data
    regardless of which pooled connection was assigned to this request.
    """
    async with async_session() as session:
        token = _session_ctx.set(session)
        try:
            # Discard any stale transaction state from the pooled connection.
            await session.rollback()
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)


_session_ctx: ContextVar[AsyncSession | None] = ContextVar("db_session_ctx", default=None)


@asynccontextmanager
async def bind_session_context(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Temporarily expose an existing session to DAO helpers without owning its transaction."""
    token = _session_ctx.set(session)
    try:
        yield session
    finally:
        _session_ctx.reset(token)


@asynccontextmanager
async def transaction(session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional boundary using contextvars."""
    if session is not None:
        token = _session_ctx.set(session)
        try:
            yield session
            if hasattr(session, "commit"):
                await session.commit()
        except Exception:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)
        return

    existing_session = _session_ctx.get()
    if existing_session is not None:
        yield existing_session
        return

    async with async_session() as session:
        token = _session_ctx.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)
