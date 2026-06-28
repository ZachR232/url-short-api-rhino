import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger("url_shortener")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
# How long (seconds) a resolved URL lives in cache.
# Short enough that DB updates eventually propagate; long enough to be useful.
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

_client: aioredis.Redis | None = None


def _get_client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


async def get_cached_url(short_code: str) -> str | None:
    """Return the cached original URL, or None on miss/error."""
    try:
        return await _get_client().get(f"url:{short_code}")
    except Exception as exc:
        # Cache errors must never break the happy path — log and continue
        logger.warning(f"Redis GET failed for short_code={short_code}: {exc}")
        return None


async def set_cached_url(short_code: str, original_url: str) -> None:
    """Cache a mapping with TTL. Errors are non-fatal."""
    try:
        await _get_client().set(f"url:{short_code}", original_url, ex=CACHE_TTL)
    except Exception as exc:
        logger.warning(f"Redis SET failed for short_code={short_code}: {exc}")


async def ping_redis() -> bool:
    """Return True if Redis is reachable."""
    try:
        return await _get_client().ping()
    except Exception as exc:
        logger.error(f"Redis ping failed: {exc}")
        return False
