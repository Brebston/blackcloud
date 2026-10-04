# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Непривілейований користувач
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app

COPY --chown=app:app . .
# /data/objects — точка монтування тому з шифрованими файлами (Docker копіює власника в новий том)
RUN chmod +x /app/entrypoint.sh && mkdir -p /app/staticfiles /data/objects \
 && chown app:app /app/staticfiles /data/objects && chmod 700 /data/objects

USER app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["web"]
