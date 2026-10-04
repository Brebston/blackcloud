#!/usr/bin/env sh
# Генерує всі секрети у ./secrets (лише якщо їх ще немає).
# Каталог secrets/ має права 700 (інші користувачі хоста не можуть у нього зайти),
# а самі файли — 644, бо Docker Compose монтує їх у контейнери з правами хоста,
# і сервіси працюють від непривілейованих користувачів. У git секрети не потрапляють.
set -eu
cd "$(dirname "$0")/.."
mkdir -p secrets certs
chmod 700 secrets

rand() { head -c "$1" /dev/urandom | base64 | tr -d '\n=+/' | head -c "$2"; }

make_secret() {
  name="$1"; value="$2"
  if [ ! -s "secrets/$name" ]; then
    printf '%s' "$value" > "secrets/$name"
    chmod 644 "secrets/$name"
    echo "  створено secrets/$name"
  fi
}

echo "Генерація секретів..."
make_secret django_secret_key       "$(rand 96 80)"
make_secret postgres_password       "$(rand 48 40)"
make_secret mail_db_password        "$(rand 48 40)"
make_secret redis_password          "$(rand 48 40)"
make_secret dovecot_master_password "$(rand 48 40)"
# Майстер-ключ шифрування файлів (AES-256-GCM). Формат: "<версія>:<base64 32 байти>"
# Для ротації додайте новий рядок з більшою версією — старі файли лишаться читабельними.
make_secret file_master_keys        "1:$(head -c 32 /dev/urandom | base64 | tr -d '\n')"
# Ключ шифрування чутливих полів у БД (TOTP-секрети тощо), формат Fernet
make_secret field_encryption_key    "$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "  створено .env з .env.example — відредагуйте DOMAIN, MAIL_DOMAIN тощо"
fi

if [ ! -s certs/fullchain.pem ]; then
  echo "  УВАГА: certs/fullchain.pem та certs/privkey.pem відсутні —"
  echo "  поштові сервіси згенерують самопідписаний сертифікат. Для продакшну"
  echo "  покладіть туди сертифікат для MAIL_HOSTNAME (напр. з certbot)."
fi
echo "Готово."
