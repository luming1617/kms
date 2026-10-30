"""服务侧 asyncpg 连接池（与 LightRAG 自己的池分离，权威层查询走这里）。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import asyncpg

from kms.config import Settings
from kms.observability.metrics import observe_db_pool_wait


class Database:
    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.postgres.dsn()
        self._max_connections = settings.postgres.max_connections
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn, min_size=1, max_size=self._max_connections
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() 尚未调用")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        started = time.perf_counter()
        async with self.pool.acquire() as connection:
            observe_db_pool_wait(time.perf_counter() - started)
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        started = time.perf_counter()
        async with self.pool.acquire() as connection, connection.transaction():
            observe_db_pool_wait(time.perf_counter() - started)
            yield connection

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        async with self.acquire() as connection:
            return cast("list[asyncpg.Record]", await connection.fetch(sql, *args))

    async def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        async with self.acquire() as connection:
            return await connection.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with self.acquire() as connection:
            return await connection.fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        async with self.acquire() as connection:
            return str(await connection.execute(sql, *args))
