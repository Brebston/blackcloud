COMPOSE ?= docker compose
DEV = $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: help init secrets build migrations migrate up up-dev down logs superuser test shell dkim mta-sts backup lock pin scan audit thumbnails backup-install backup-enable backup-now backup-check backup-list

help:
	@echo "make init       — перший запуск: секрети, збірка, міграції, старт"
	@echo "make up         — запустити (продакшн)"
	@echo "make up-dev     — запустити локально на https://localhost"
	@echo "make superuser  — створити адміністратора"
	@echo "make dkim       — показати DNS-запис DKIM"
	@echo "make mta-sts    — DNS-записи й політика MTA-STS / TLS-RPT"
	@echo "make test       — тести бекенду"
	@echo "make backup     — резервна копія БД"
	@echo "make lock       — зафіксувати залежності з хешами (requirements.lock, package-lock.json)"
	@echo "make pin        — закріпити Docker-образи за digest у .env"
	@echo "make scan       — сканування образів на вразливості (Trivy)"
	@echo "make audit      — перевірка залежностей (pip-audit, npm audit)"
	@echo "make backup-install / backup-enable / backup-now / backup-check / backup-list — резервні копії (docs/BACKUP.md)"

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

mta-sts:
	$(COMPOSE) exec backend python manage.py mta_sts_dns

test:
	$(COMPOSE) run --rm --no-deps --user "$$(id -u):$$(id -g)" -v ./backend:/app -e DJANGO_TEST=1 -e HOME=/tmp --entrypoint pytest backend -p no:cacheprovider

backup:
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U blackcloud -Fc blackcloud > backups/db-$$(date +%Y%m%d-%H%M%S).dump
	@echo "Збережено у backups/ (зашифруйте перед відправкою за межі сервера)"

# ─── Резервні копії поза сервером (restic → Hetzner Storage Box), див. docs/BACKUP.md ───
# Скрипт копіюється в /usr/local/sbin (власник root): root ніколи не виконує файл із репозиторію
backup-install:
	sudo install -o root -g root -m 755 scripts/backup.sh /usr/local/sbin/blackcloud-backup
	sudo install -o root -g root -m 644 scripts/systemd/blackcloud-backup*.service scripts/systemd/blackcloud-backup*.timer /etc/systemd/system/
	sed "s|/opt/blackcloud|$(CURDIR)|g" scripts/systemd/blackcloud-backup-request.path \
	  | sudo tee /etc/systemd/system/blackcloud-backup-request.path >/dev/null
	# backup-state/status — читає адмінка; request/ — куди вона пише запит (uid 10001 = app у контейнері)
	sudo install -d -m 755 -o root -g root backup-state backup-state/status
	sudo install -d -m 700 -o 10001 -g 10001 backup-state/request
	sudo systemctl daemon-reload
	@echo "Встановлено. Далі: sudo blackcloud-backup init (див. docs/BACKUP.md)"

backup-enable:
	sudo systemctl enable --now blackcloud-backup.timer blackcloud-backup-check.timer blackcloud-backup-request.path
	sudo blackcloud-backup status
	systemctl list-timers 'blackcloud-backup*'

backup-now:
	sudo systemctl start blackcloud-backup.service
	sudo journalctl -u blackcloud-backup -n 20 --no-pager

backup-check:
	sudo systemctl start blackcloud-backup-check.service
	sudo journalctl -u blackcloud-backup-check -n 20 --no-pager

backup-list:
	sudo blackcloud-backup snapshots

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
	for img in blackcloud/backend blackcloud/thumbnailer blackcloud/frontend blackcloud/clamav blackcloud/postfix blackcloud/dovecot blackcloud/rspamd; do \
	  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image \
	    --severity HIGH,CRITICAL --ignore-unfixed --quiet $$img:latest; \
	done

audit:
	docker run --rm -v ./backend:/src -w /src python:3.12-slim-bookworm sh -c \
	  "pip install -q pip-audit && if [ -f requirements.lock ]; then pip-audit --require-hashes -r requirements.lock; else pip-audit -r requirements.txt; fi"
	# Без «|| true»: знайдені вразливості (high і вище) зупиняють перевірку. npm audit потребує package-lock.json (make lock)
	docker run --rm -v ./frontend:/app -w /app node:22-alpine sh -c \
	  "[ -f package-lock.json ] || { echo 'Немає package-lock.json — спершу виконайте make lock'; exit 1; }; npm audit --omit=dev --audit-level=high"
