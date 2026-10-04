COMPOSE ?= docker compose
DEV = $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help init secrets build migrations migrate up up-dev down logs superuser test shell dkim backup

help:
	@echo "make init       — перший запуск: секрети, збірка, міграції, старт"
	@echo "make up         — запустити (продакшн)"
	@echo "make up-dev     — запустити локально на https://localhost"
	@echo "make superuser  — створити адміністратора"
	@echo "make dkim       — показати DNS-запис DKIM"
	@echo "make test       — тести бекенду"
	@echo "make backup     — резервна копія БД"

secrets:
	@sh scripts/init-secrets.sh

build:
	$(COMPOSE) build

# Генерує нові міграції після зміни моделей (закомітьте їх)
migrations:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v ./backend:/app --entrypoint python backend manage.py makemigrations accounts core storage calendars chat mail

migrate:
	$(COMPOSE) run --rm backend manage migrate

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

dkim:
	$(COMPOSE) exec rspamd sh -c 'cat /var/lib/rspamd/dkim/*.txt'

test:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v ./backend:/app -e DJANGO_TEST=1 -e HOME=/tmp --entrypoint pytest backend -p no:cacheprovider

backup:
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U blackcloud -Fc blackcloud > backups/db-$$(date +%Y%m%d-%H%M%S).dump
	@echo "Збережено у backups/ (зашифруйте перед відправкою за межі сервера)"
