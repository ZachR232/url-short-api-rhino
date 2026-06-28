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

logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("url_shortener")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SHORT_CODE_LENGTH = int(os.getenv("SHORT_CODE_LENGTH", "7"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initialising database tables")
    await create_tables()
    logger.info("Startup complete")
    yield
    logger.info("Shutting down")


app = FastAPI(title="URL Shortener", lifespan=lifespan)


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


def generate_short_code(length: int = SHORT_CODE_LENGTH) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=length))


# /health MUST be defined before /{short_code}
# FastAPI matches routes top to bottom — if /{short_code} comes first,
# it captures /health as a short code lookup and never reaches this handler.
@app.get("/health", response_model=HealthResponse)
async def health_check():
    db_ok = await ping_db()
    redis_ok = await ping_redis()

    status = "healthy" if (db_ok and redis_ok) else "degraded"
    http_status = 200 if (db_ok and redis_ok) else 503

    response = HealthResponse(status=status, postgres=db_ok, redis=redis_ok)

    if http_status != 200:
        logger.warning(f"Health check degraded postgres={db_ok} redis={redis_ok}")
        raise HTTPException(status_code=http_status, detail=response.model_dump())

    return response


@app.post("/shorten", response_model=ShortenResponse, status_code=201)
async def shorten_url(payload: ShortenRequest):
    original_url = str(payload.url)

    async with get_db_connection() as conn:
        row = await conn.fetchrow(
            "SELECT short_code FROM urls WHERE original_url = $1", original_url
        )
        if row:
            short_code = row["short_code"]
            logger.info(f"Returning existing short_code={short_code} for url={original_url}")
        else:
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


@app.get("/{short_code}")
async def redirect_url(short_code: str):
    cached = await get_cached_url(short_code)
    if cached:
        logger.info(f"Cache hit short_code={short_code}")
        return RedirectResponse(url=cached, status_code=302)

    logger.info(f"Cache miss short_code={short_code} — querying DB")
    async with get_db_connection() as conn:
        row = await conn.fetchrow(
            "SELECT original_url FROM urls WHERE short_code = $1", short_code
        )

    if not row:
        logger.warning(f"Not found short_code={short_code}")
        raise HTTPException(status_code=404, detail="Short URL not found")

    original_url = row["original_url"]
    await set_cached_url(short_code, original_url)
    return RedirectResponse(url=original_url, status_code=302)
