# URL Shortener

A containerised URL shortener built with FastAPI, PostgreSQL, and Redis.

## Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| API | Python 3.12 + FastAPI | Async-native, automatic OpenAPI docs, fast to iterate |
| Database | PostgreSQL 16 | ACID guarantees, durable storage for URL mappings |
| Cache | Redis 7 | Sub-millisecond reads for hot short codes |
| Container runtime | Docker + Compose | Single command to run the whole stack |

---

## Running the project

**Requirements:** Docker with the Compose plugin (tested on Docker 26+). Nothing else needed.

```bash
# 1. Clone the repo
git clone <repo-url> && cd url-shortener

# 2. Create your environment file
cp .env.example .env
# Edit .env — at minimum change POSTGRES_PASSWORD

# 3. Start the stack
docker compose up --build

# 4. The API is now available at http://localhost:8000
```

### Useful commands

```bash
# Run in the background
docker compose up --build -d

# Follow API logs only
docker compose logs -f api

# Stop everything (data is preserved)
docker compose down

# Stop and wipe the database volume
docker compose down -v
```

---

## API Reference

Interactive docs are available at **http://localhost:8000/docs** once the stack is running.

### `POST /shorten`

Create a short URL.

```bash
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/some/very/long/path"}'
```

Response:
```json
{
  "short_code": "aB3kR7x",
  "short_url": "http://localhost:8000/aB3kR7x"
}
```

### `GET /{short_code}`

Resolves and redirects (HTTP 302) to the original URL.

```bash
curl -L http://localhost:8000/aB3kR7x
```

### `GET /health`

Returns the health status of the API and its dependencies.

```bash
curl http://localhost:8000/health
```

Response (healthy):
```json
{
  "status": "healthy",
  "postgres": true,
  "redis": true
}
```

Returns HTTP 503 if either dependency is unreachable.

---

## Architecture decisions

### Multi-stage Dockerfile

The builder stage installs dependencies into a virtual environment. The runtime stage copies only the venv and the application code — no pip, no compiler toolchain, no build cache. This reduces the final image size and the attack surface.

### Startup ordering

`depends_on` with `condition: service_healthy` means the API container does not start until PostgreSQL passes `pg_isready`. Without this, the API races the database on startup and fails with a connection error.

### Redis as a read-through cache

Redirect requests (`GET /{short_code}`) check Redis first. On a cache miss the DB is queried and the result is written to Redis with a TTL. Cache errors are caught and logged — they never break the redirect path. This is a deliberate decision: Redis is a performance layer, not a source of truth.

### Non-root container user

The runtime image creates a dedicated `appuser` (uid 1001) and drops privileges before `uvicorn` starts. If a vulnerability in a dependency allowed code execution, the attacker would not have root inside the container.

### Resource limits

Each service has `deploy.resources.limits` set. This prevents one misbehaving service from starving the others on the host. Redis's memory limit is set 25% above `maxmemory` so the OS does not OOM-kill the process before Redis's own eviction policy can act.

### LRU eviction in Redis

`maxmemory-policy allkeys-lru` means Redis evicts the least-recently-used key when it hits its memory limit. The cache stays self-managing — no external cleanup job is needed.

### Idempotent DB schema

`CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` on startup mean the app is safe to restart without migration tooling. For a production system with schema evolution, Alembic or Flyway would replace this.

### Structured logging

Every log line is a JSON object. This makes logs parseable by tools like Loki, Datadog, or CloudWatch Logs Insights without a custom parser. Fields like `method`, `path`, `status`, and `duration_ms` are included on every request.

---

## On-call runbook

### The API returns 503

Check the health endpoint:
```bash
curl http://localhost:8000/health
```

- `postgres: false` → check `docker compose logs postgres`. Look for OOM kills or disk-full errors. Verify the volume is mounted: `docker compose exec postgres df -h /var/lib/postgresql/data`.
- `redis: false` → check `docker compose logs redis`. Usually a memory issue. Run `docker compose exec redis redis-cli info memory`.
- Both false → the API container itself may have lost network access to the internal Docker network. Restart with `docker compose restart api`.

### Short codes return 404 unexpectedly

1. Confirm the code exists in the database:
   ```bash
   docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
     -c "SELECT * FROM urls WHERE short_code = 'YOUR_CODE';"
   ```
2. If it exists in the DB but returns 404, the API may be pointing at the wrong DB. Check `DATABASE_URL` in `docker compose config`.

### Redirects are slow (>200ms)

Cache is likely cold or disabled. Check Redis:
```bash
docker compose exec redis redis-cli info stats | grep keyspace_hits
```

A low hit rate means either TTL is too short or Redis was restarted and the cache is rebuilding. This is self-healing — hit rates recover as traffic flows.

### Disk usage growing on the host

The PostgreSQL volume grows with every new URL. Check size:
```bash
docker system df -v | grep postgres_data
```

For a long-running deployment, add a cleanup job to delete rows older than N days. Redis is bounded by `maxmemory` and will not grow unboundedly.
