# URL Shortener

A containerised URL shortener built with FastAPI, PostgreSQL, and Redis, with built-in monitoring via Gatus.

## Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| API | Python 3.12 + FastAPI | Async-native, automatic OpenAPI docs, fast to iterate |
| Database | PostgreSQL 16 | ACID guarantees, durable storage for URL mappings |
| Cache | Redis 7 | Sub-millisecond reads for hot short codes |
| Monitoring | Gatus | Config-file based health dashboard, no manual setup |
| Container runtime | Docker + Compose | Single command to run the whole stack |

---

## Running the project

**Requirements:** Docker with the Compose plugin and `make`. Nothing else needed.

```bash
# 1. Clone the repo
git clone <repo-url> && cd rhino_claude

# 2. Initialise — creates .env automatically from .env.example
make init

# 3. Start the stack
make up
```

The `.env` file is created automatically on `make init`. You can edit it to change passwords or ports before running `make up`.

---

## Make commands

All common tasks are available as `make` commands — no need to remember long Docker or curl commands.

| Command | What it does |
|---|---|
| `make init` | Creates `.env` from `.env.example` if it doesn't exist |
| `make up` | Builds and starts all containers |
| `make down` | Stops all containers (data is preserved) |
| `make logs` | Tails logs from all containers |
| `make health` | Hits the API health endpoint and pretty-prints the response |
| `make shorten url=https://example.com` | Creates a short URL |
| `make redirect code=aB3kR7x` | Follows a short code and prints the destination |
| `make urls` | Lists all shortened URLs stored in the database |
| `make delete code=aB3kR7x` | Deletes a short code from the database |

---

## API reference

Interactive docs are available at **http://localhost:8000/docs** once the stack is running.

### `POST /shorten`

Create a short URL.

```bash
make shorten url=https://www.example.com/some/very/long/path

# or with raw curl:
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
make redirect code=aB3kR7x

# or with raw curl:
curl -L http://localhost:8000/aB3kR7x
```

### `GET /health`

Returns the health status of the API and its dependencies.

```bash
make health

# or with raw curl:
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

## Monitoring dashboard

Gatus runs as an independent monitoring container and checks all three services from outside the API. If the API itself goes down, Gatus keeps running and reports it.

Open **http://localhost:8080** to see the live dashboard.

Monitors configured out of the box:
- **API Health** — HTTP check on `/health`, expects 200 and `status: healthy`
- **Postgres** — TCP check on port 5432
- **Redis** — TCP check on port 6379

No manual setup needed — monitors are pre-configured in `gatus.yml` and load automatically on `make up`.

---

## Some Architecture decisions

1. Route ordering in FastAPI

`GET /health` is defined before `GET /{short_code}` in `main.py`. FastAPI matches routes top to bottom — if the wildcard route came first, every request to `/health` would be treated as a short code lookup and return 404.

2. Startup ordering

`depends_on` with `condition: service_healthy` means the API container does not start until PostgreSQL passes `pg_isready`. Without this, the API races the database on startup and fails with a connection error.

3. Redis as a read-through cache

Redirect requests check Redis first. On a cache miss the DB is queried and the result is written to Redis with a TTL. Cache errors are caught and logged — they never break the redirect path. Redis is a performance layer, not a source of truth.

4. #hardening - Non-root container user

The runtime image creates a dedicated `appuser` (uid 1001) and drops privileges before uvicorn starts. If a vulnerability in a dependency allowed code execution, the attacker would not have root inside the container.

5. #hardening - Resource limits

Each service has `deploy.resources.limits` set. This prevents one misbehaving service from starving the others on the host. Redis memory limit is set 25% above `maxmemory` so the OS does not OOM-kill the process before Redis's own eviction policy can act.

6. #hardening - LRU eviction in Redis

`maxmemory-policy allkeys-lru` means Redis evicts the least-recently-used key when it hits its memory limit. The cache stays self-managing — no external cleanup job needed.

7. #hardening - Self-healing

Every service has `restart: unless-stopped`. If a container crashes, Docker restarts it automatically. To test:


8. #observability - Structured logging

Every log line is a JSON object, parseable by tools like Loki, Datadog, or CloudWatch Logs Insights without a custom parser. Fields like `method`, `path`, `status`, and `duration_ms` are included on every request.

### Idempotent DB schema

`CREATE TABLE IF NOT EXISTS` on startup means the app is safe to restart without migration tooling.

### Makefile as developer interface

All common tasks are wrapped in `make` targets so anyone cloning the repo can operate the stack without reading documentation first. The `.env` file is created automatically on `make init` so there is no manual setup step.

---

## On-call runbook

### The API returns 503

Check the health endpoint:
```bash
make health
```

- `postgres: false` — check `docker compose logs postgres`. Look for OOM kills or disk-full errors.
- `redis: false` — check `docker compose logs redis`. Usually a memory issue. Run `docker compose exec redis redis-cli info memory`.
- Both false — the API may have lost network access. Restart with `docker compose restart api`.

### Postgres disk is full

Check disk usage:
```bash
docker compose exec postgres df -h /var/lib/postgresql/data
```

Clean up old records:
```bash
docker compose exec postgres psql -U appuser -d urlshortener \
  -c "DELETE FROM urls WHERE created_at < NOW() - INTERVAL '90 days';"
docker compose exec postgres psql -U appuser -d urlshortener \
  -c "VACUUM ANALYZE urls;"
```

### Redis memory full

Check memory usage:
```bash
docker compose exec redis redis-cli info memory
```

Key fields: `used_memory_human` vs `maxmemory_human`. If eviction is happening constantly, either increase `maxmemory` in `docker-compose.yml` or reduce `CACHE_TTL_SECONDS` in `.env`.

### Redirects are slow

Check cache hit rate:
```bash
docker compose exec redis redis-cli info stats | grep -E "keyspace_hits|keyspace_misses"
```

A low hit rate means the cache is cold — self-healing as traffic flows through.

### Short codes return 404 unexpectedly

Confirm the code exists in the database:
```bash
make urls
```

If it exists in the DB but returns 404, check that `BASE_URL` in `.env` matches the host you are running on.

### Container keeps restarting

```bash
docker compose ps
docker compose logs --tail=50 api
```

Common causes: wrong DB password in `.env`, DB not ready yet, or an unhandled exception on startup.

### Postgres connection pool exhausted

Check active connections:
```bash
docker compose exec postgres psql -U appuser -d urlshortener \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname = 'urlshortener';"
```

Pool is configured at `max_size=10` in `database.py`. Increase if consistently hitting the limit.
