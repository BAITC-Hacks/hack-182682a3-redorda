PYTHON := .venv/bin/python

.PHONY: setup migrate backend frontend worker check schema check-integration compose-check
setup:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements.txt
	npm --prefix frontend ci

migrate:
	$(PYTHON) backend/manage.py migrate

backend:
	$(PYTHON) backend/manage.py runserver 127.0.0.1:8000

frontend:
	npm --prefix frontend run dev

worker:
	.venv/bin/celery --workdir backend -A config worker --loglevel=info --pool=prefork --concurrency=1

check-integration:
	$(PYTHON) backend/manage.py shell -c 'from django.db import connection; assert connection.vendor == "postgresql", "Set DATABASE_URL to a dedicated PostgreSQL database"'
	REDORDA_LIVE_TESTS=1 $(PYTHON) -m pytest -q backend/tests/integration

compose-check:
	docker compose config --quiet

schema:
	$(PYTHON) backend/manage.py spectacular --file docs/openapi.yaml --validate --fail-on-warn

check:
	.venv/bin/ruff check backend packages scripts agent.py
	$(PYTHON) backend/manage.py check
	$(PYTHON) backend/manage.py makemigrations --check --dry-run
	$(PYTHON) -m pytest -q
	npm --prefix frontend test
	npm --prefix frontend run build
