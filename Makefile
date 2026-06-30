.PHONY: init up down logs health shorten redirect urls delete test

# Automatically create .env from .env.example if it doesn't exist
.env:
	cp .env.example .env
	@echo ".env created from .env.example"

init: .env
	@echo "Project ready. Run 'make up' to start."

up: .env
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

health:
	curl -s http://localhost:8000/health | python3 -m json.tool

shorten:
	curl -s -X POST http://localhost:8000/shorten \
		-H "Content-Type: application/json" \
		-d '{"url": "$(url)"}' | python3 -m json.tool

redirect:
	curl -L http://localhost:8000/$(code)

urls:
	docker compose exec postgres psql -U appuser -d urlshortener \
		-c "SELECT short_code, original_url, created_at FROM urls ORDER BY created_at DESC;"

delete:
	docker compose exec postgres psql -U appuser -d urlshortener \
		-c "DELETE FROM urls WHERE short_code = '$(code)';"

# Run the integration test suite against a live stack.
# Assumes `make up` is already running (or starts it if not).
test:
	docker compose up --build -d
	pip install -q -r requirements-dev.txt
	pytest tests/ -v
