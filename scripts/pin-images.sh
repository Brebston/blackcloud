#!/usr/bin/env sh
# Закріплює всі сторонні Docker-образи за digest (sha256) і записує їх у .env.
# Після цього `docker compose build/pull` бере саме ці байти, навіть якщо тег
# у реєстрі підмінили. Оновлення: запустіть скрипт знову і перевірте зміни (git diff .env не видно —
# порівняйте вивід скрипта).
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env не знайдено (make secrets)"; exit 1; }

pin() {
  var="$1"; ref="$2"
  docker pull -q "$ref" >/dev/null
  digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$ref")"
  if grep -q "^$var=" .env; then
    sed -i.bak "s|^$var=.*|$var=$digest|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$var" "$digest" >> .env
  fi
  echo "  $var=$digest"
}

echo "Закріплення образів за digest..."
pin TRAEFIK_IMAGE  "traefik:v3.1"
pin POSTGRES_IMAGE "postgres:16-alpine"
pin REDIS_IMAGE    "redis:7-alpine"
pin PYTHON_IMAGE   "python:3.12-slim-bookworm"
pin NODE_IMAGE     "node:22-alpine"
pin NGINX_IMAGE    "nginxinc/nginx-unprivileged:1.27-alpine"
pin DEBIAN_IMAGE   "debian:bookworm-slim"
if grep -q '^COMPOSE_PROFILES=.*office' .env; then
  pin OFFICE_IMAGE "onlyoffice/documentserver:9.4.0"
fi
echo "Готово. Перезберіть: docker compose build --pull && docker compose up -d"
