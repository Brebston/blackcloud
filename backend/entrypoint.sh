#!/bin/sh
set -eu

case "${1:-web}" in
  web)
    python manage.py check --deploy --fail-level ERROR
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput -v 0
    exec uvicorn config.asgi:application \
      --host 0.0.0.0 --port 8000 \
      --workers "${WEB_WORKERS:-4}" \
      --proxy-headers --forwarded-allow-ips '*' \
      --no-server-header \
      --timeout-keep-alive 30
    ;;
  worker)
    exec celery -A config worker -l INFO --concurrency "${WORKER_CONCURRENCY:-2}" -Q default,scan
    ;;
  beat)
    exec celery -A config beat -l INFO --schedule /tmp/celerybeat-schedule
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
