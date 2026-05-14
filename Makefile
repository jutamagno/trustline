.PHONY: up down test lint api seed demo eval logs

up:
	docker compose -f infra/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker-compose.yml down -v

test:
	python -m pytest tests/ -v --tb=short

lint:
	ruff check trustline/ api/ dags/ tests/

api:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

seed:
	python scripts/seed_data.py

demo:
	python scripts/run_demo.py

eval:
	python eval/run_eval.py

logs:
	docker compose -f infra/docker-compose.yml logs -f api airflow-scheduler
