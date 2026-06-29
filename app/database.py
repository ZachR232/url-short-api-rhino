import logging
import os
from contextlib import asynccontextmanager

import asyncpg

logger = logging.getLogger("url_shortener")

DATABASE_URL = os.getenv("DATABASE_URL")


async def _get_pool() -> asyncpg.Pool:
    """Create a connection pool. Called lazily so tests can override DATABASE_URL."""
    return await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)


# Module-level pool — initialised on first use
_pool: asyncpg.Pool | None = None


async def _pool_instance() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await _get_pool()
    return _pool


@asynccontextmanager
async def get_db_connection():
    """Yield a single connection from the pool as a context manager."""
    pool = await _pool_instance()
    async with pool.acquire() as conn:
        yield conn


async def create_tables():
    """Idempotently create the urls table on startup."""
    async with get_db_connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS urls (
                id          SERIAL PRIMARY KEY,
                short_code  VARCHAR(20)  NOT NULL UNIQUE,
                original_url TEXT        NOT NULL,
                created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_short_code ON urls (short_code);
            """
        )
    logger.info("Database tables ready")


async def ping_db() -> bool:
    """Return True if the database is reachable."""
    try:
        async with get_db_connection() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception as exc:
        logger.error(
            f"Postgres is unreachable — host=postgres port=5432 "
            f"reason={type(exc).__name__}: {exc}"
        )
        return False
