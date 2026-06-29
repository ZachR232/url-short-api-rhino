.PHONY: up down logs health shorten redirect

up:
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

include .env
export
