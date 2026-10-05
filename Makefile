COMPOSE ?= docker compose
DEV = $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help init secrets build migrations migrate up up-dev down logs superuser test shell dkim backup lock pin scan audit thumbnails

help:
	@echo "make init       — перший запуск: секрети, збірка, міграції, старт"
	@echo "make up         — запустити (продакшн)"
	@echo "make up-dev     — запустити локально на https://localhost"
	@echo "make superuser  — створити адміністратора"
	@echo "make dkim       — показати DNS-запис DKIM"
	@echo "make test       — тести бекенду"
	@echo "make backup     — резервна копія БД"
	@echo "make lock       — зафіксувати залежності з хешами (requirements.lock, package-lock.json)"
	@echo "make pin        — закріпити Docker-образи за digest у .env"
	@echo "make scan       — сканування образів на вразливості (Trivy)"
	@echo "make audit      — перевірка залежностей (pip-audit, npm audit)"

secrets:
	@sh scripts/init-secrets.sh

build:
	$(COMPOSE) build

# Генерує нові міграції після зміни моделей (закомітьте їх)
migrations:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v ./backend:/app --entrypoint python backend manage.py makemigrations accounts core storage calendars chat mail

# Міграції виконує окремий сервіс від імені власника БД (веб працює під обмеженою роллю bc_app)
migrate:
	$(COMPOSE) run --rm migrate

init: secrets build
	$(COMPOSE) up -d postgres redis
	$(MAKE) migrate
	$(COMPOSE) up -d
	@echo "Тепер створіть адміністратора: make superuser"

up:
	$(COMPOSE) up -d

up-dev:
	$(DEV) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

superuser:
	$(COMPOSE) exec backend python manage.py createsuperuser

shell:
	$(COMPOSE) exec backend python manage.py shell

thumbnails:
	$(COMPOSE) exec worker python manage.py generate_thumbnails

dkim:
	$(COMPOSE) exec rspamd sh -c 'cat /var/lib/rspamd/dkim/*.txt'

test:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v ./backend:/app -e DJANGO_TEST=1 -e HOME=/tmp --entrypoint pytest backend -p no:cacheprovider

backup:
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U blackcloud -Fc blackcloud > backups/db-$$(date +%Y%m%d-%H%M%S).dump
	@echo "Збережено у backups/ (зашифруйте перед відправкою за межі сервера)"

# ─── Ланцюг постачання ─────────────────────────────────────────
lock:
	docker run --rm -v ./backend:/src -w /src python:3.12-slim-bookworm sh -c \
	  "pip install -q pip-tools && pip-compile -q --generate-hashes --strip-extras --allow-unsafe -o requirements.lock requirements.txt"
	docker run --rm -v ./frontend:/app -w /app node:22-alpine npm install --package-lock-only --ignore-scripts
	@echo "Закомітьте backend/requirements.lock і frontend/package-lock.json, потім: docker compose build"

pin:
	@sh scripts/pin-images.sh

scan:
	$(COMPOSE) build
	for img in blackcloud/backend blackcloud/frontend blackcloud/clamav blackcloud/postfix blackcloud/dovecot blackcloud/rspamd; do \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image \
	    --severity HIGH,CRITICAL --ignore-unfixed --quiet $$img:latest; \
	done

audit:
	docker run --rm -v ./backend:/src -w /src python:3.12-slim-bookworm sh -c \
	  "pip install -q pip-audit && pip-audit -r requirements.txt"
	docker run --rm -v ./frontend:/app -w /app node:22-alpine sh -c "npm audit --omit=dev || true"
