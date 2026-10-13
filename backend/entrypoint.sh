#!/bin/sh
set -eu

case "${1:-web}" in
  migrate)
    # Одноразовий сервіс від імені власника БД: міграції + обмежена роль bc_app
    python manage.py migrate --noinput
    python manage.py ensure_db_roles
    ;;
  web)
    # Міграції тут НЕ виконуються: веб працює під роллю bc_app без прав на DDL
    python manage.py check --deploy --fail-level ERROR
    python manage.py collectstatic --noinput -v 0
    # X-Forwarded-* приймаються лише від Traefik (фіксована адреса в мережі edge).
    # Журнал доступу вимкнено: у ньому опинялись би шляхи з токенами (Traefik веде свій).
    exec uvicorn config.asgi:application \
      --host 0.0.0.0 --port 8000 \
      --workers "${WEB_WORKERS:-4}" \
      --proxy-headers --forwarded-allow-ips "${TRUSTED_PROXY_IP:-172.31.250.2}" \
      --no-access-log \
      --no-server-header \
      --timeout-keep-alive 30
    ;;
  dav)
    # Окремий процес для WebDAV (/dav/): масове копіювання з Finder/Windows навантажує
    # лише його і не забирає воркери вебінтерфейсу (вхід, API).
    python manage.py check --deploy --fail-level ERROR
    exec uvicorn config.asgi:application \
      --host 0.0.0.0 --port 8000 \
      --workers "${DAV_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips "${TRUSTED_PROXY_IP:-172.31.250.2}" \
      --no-access-log \
      --no-server-header \
      --timeout-keep-alive 30
    ;;
  worker)
    exec celery -A config worker -l INFO --concurrency "${WORKER_CONCURRENCY:-2}" -Q default,scan
    ;;
  pusher)
    # Лише черга "push": шифрує й надсилає Web Push у сервіси браузерів (мережа egress).
    # Без ключів шифрування файлів і без пароля БД (SERVICE_ROLE=pusher у compose).
    exec celery -A config worker -l INFO --concurrency "${PUSH_CONCURRENCY:-2}" -Q push -n "pusher@%h"
    ;;
  beat)
    exec celery -A config beat -l INFO --schedule /tmp/celerybeat-schedule
    ;;
  thumbnailer)
    exec python thumbsvc.py
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
