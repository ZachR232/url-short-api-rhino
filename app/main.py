import logging
import os
import random
import string
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from .cache import get_cached_url, set_cached_url, ping_redis
from .database import create_tables, get_db_connection, ping_db
from .schemas import ShortenRequest, ShortenResponse, HealthResponse

# ---------------------------------------------------------------------------
# Structured logging — every line is JSON so log aggregators can parse it
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("url_shortener")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SHORT_CODE_LENGTH = int(os.getenv("SHORT_CODE_LENGTH", "7"))


# ---------------------------------------------------------------------------
# Lifespan: runs once on startup — creates DB tables if they don't exist
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising database tables")
    await create_tables()
    logger.info("Startup completed - Let's go!")
    yield
    logger.info("Shutting down - no more shortening for now...")


app = FastAPI(title="URL Shortener - To make your life Easy", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request logging middleware — logs method, path, status, and duration
# Useful for debugging at 2am without needing an APM tool
# ---------------------------------------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 2)
    logger.info(
        f"method={request.method} path={request.url.path} "
        f"status={response.status_code} duration_ms={duration_ms}"
    )
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def generate_short_code(length: int = SHORT_CODE_LENGTH) -> str:
    """Generate a random alphanumeric short code."""
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/shorten", response_model=ShortenResponse, status_code=201)
async def shorten_url(payload: ShortenRequest):
    """Create a short URL for the given original URL."""
    original_url = str(payload.url)

    async with get_db_connection() as conn:
        # Check if this URL was already shortened — return existing code
        row = await conn.fetchrow(
            "SELECT short_code FROM urls WHERE original_url = $1", original_url
        )
        if row:
            short_code = row["short_code"]
            logger.info(f"Returning existing short_code={short_code} for url={original_url}")
        else:
            # Generate a unique short code (retry on collision — extremely rare)
            for _ in range(5):
                short_code = generate_short_code()
                exists = await conn.fetchval(
                    "SELECT 1 FROM urls WHERE short_code = $1", short_code
                )
                if not exists:
                    break
            else:
                logger.error("Failed to generate a unique short code after 5 attempts")
                raise HTTPException(status_code=500, detail="Could not generate a unique short code")

            await conn.execute(
                "INSERT INTO urls (short_code, original_url) VALUES ($1, $2)",
                short_code,
                original_url,
            )
            logger.info(f"Created short_code={short_code} for url={original_url}")

    return ShortenResponse(short_url=f"{BASE_URL}/{short_code}", short_code=short_code)

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Deep health check — verifies connectivity to both Postgres and Redis.
    Returns 200 only when both are reachable.
    On-call engineers can hit this endpoint to quickly isolate where a
    failure is coming from.
    """
    db_ok = await ping_db()
    redis_ok = await ping_redis()

    status = "healthy" if (db_ok and redis_ok) else "degraded"
    http_status = 200 if (db_ok and redis_ok) else 503

    response = HealthResponse(status=status, postgres=db_ok, redis=redis_ok)

    if http_status != 200:
        logger.warning(f"Health check degraded postgres={db_ok} redis={redis_ok}")
        raise HTTPException(status_code=http_status, detail=response.model_dump())

    return response

@app.get("/{short_code}")
async def redirect_url(short_code: str):
    """Resolve a short code and redirect to the original URL."""

    # 1. Try cache first
    cached = await get_cached_url(short_code)
    if cached:
        logger.info(f"Cache hit short_code={short_code}")
        return RedirectResponse(url=cached, status_code=302)

    # 2. Cache miss — hit the database
    logger.info(f"Cache miss short_code={short_code} — querying DB")
    async with get_db_connection() as conn:
        row = await conn.fetchrow(
            "SELECT original_url FROM urls WHERE short_code = $1", short_code
        )

    if not row:
        logger.warning(f"Not found short_code={short_code}")
        raise HTTPException(status_code=404, detail="Short URL not found")

    original_url = row["original_url"]

    # 3. Populate cache for next time
    await set_cached_url(short_code, original_url)

    return RedirectResponse(url=original_url, status_code=302)



