"""Async engine and session handling."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api import config
from api.storage.models import Base

_engine = create_async_engine(config.DATABASE_URL, future=True)
_session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with _session_factory() as s:
        yield s


async def dispose() -> None:
    await _engine.dispose()
