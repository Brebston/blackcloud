# syntax=docker/dockerfile:1.7
# Базовий образ можна закріпити за digest: scripts/pin-images.sh записує PYTHON_IMAGE у .env
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libmagic1 tini poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock* ./
# requirements.lock (make lock) містить точні версії з хешами: підмінений пакет не встановиться
RUN if [ -f requirements.lock ]; then pip install --require-hashes -r requirements.lock; \
    else pip install -r requirements.txt; fi

# Непривілейований користувач
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home /app --shell /usr/sbin/nologin app

# Код належить root: навіть за помилки в правах процес app не може його змінити
COPY . .
# /data/objects — точка монтування тому з шифрованими файлами (Docker копіює власника в новий том)
# Права з хоста можуть бути 600/700 (напр. після копіювання на Mac): робимо код читабельним
# для всіх, але змінювати його може лише root
RUN chmod -R a+rX,go-w /app && chmod 755 /app/entrypoint.sh && mkdir -p /app/staticfiles /data/objects \
 && chown app:app /app/staticfiles /data/objects && chmod 700 /data/objects

USER app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["web"]
