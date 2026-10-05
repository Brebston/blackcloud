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
# Пароль обмеженої ролі bc_app (веб/воркер працюють без прав власника БД)
make_secret app_db_password         "$(rand 48 40)"
make_secret mail_db_password        "$(rand 48 40)"
make_secret redis_password          "$(rand 48 40)"
make_secret dovecot_master_password "$(rand 48 40)"
# Майстер-ключ шифрування файлів (AES-256-GCM). Формат: "<версія>:<base64 32 байти>"
# Для ротації додайте новий рядок з більшою версією — старі файли лишаться читабельними.
make_secret file_master_keys        "1:$(head -c 32 /dev/urandom | base64 | tr -d '\n')"
# Ключ шифрування чутливих полів у БД (TOTP-секрети тощо), формат Fernet
make_secret field_encryption_key    "$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')"

# Глобальна пара EC-ключів Dovecot mail_crypt (шифрування листів на диску)
if [ ! -s secrets/mail_crypt_private_key ]; then
  if command -v openssl >/dev/null 2>&1; then
    umask 077
    openssl ecparam -name prime256v1 -genkey 2>/dev/null | openssl pkey -out secrets/mail_crypt_private_key
    openssl pkey -in secrets/mail_crypt_private_key -pubout -out secrets/mail_crypt_public_key
    umask 022
    chmod 644 secrets/mail_crypt_private_key secrets/mail_crypt_public_key
    echo "  створено secrets/mail_crypt_{private,public}_key"
  else
    : > secrets/mail_crypt_private_key; : > secrets/mail_crypt_public_key
    chmod 644 secrets/mail_crypt_private_key secrets/mail_crypt_public_key
    echo "  УВАГА: openssl не знайдено — шифрування листів на диску вимкнене"
  fi
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "  створено .env з .env.example — відредагуйте DOMAIN, MAIL_DOMAIN тощо"
fi

# Онлайн-редактор документів (ONLYOFFICE): секрет JWT між Django і Document Server
if [ -f .env ] && ! grep -q '^OFFICE_JWT_SECRET=' .env; then
  {
    echo ""
    echo "# ─── Онлайн-редактор документів (ONLYOFFICE, ~2–3 ГБ RAM) ───"
    echo "# Щоб вимкнути: OFFICE_ENABLED=false і прибрати office з COMPOSE_PROFILES"
    echo "OFFICE_ENABLED=true"
    echo "COMPOSE_PROFILES=office"
    echo "OFFICE_JWT_SECRET=$(rand 48 40)"
  } >> .env
  echo "  додано налаштування ONLYOFFICE у .env"
fi

if [ ! -s certs/fullchain.pem ]; then
  echo "  УВАГА: certs/fullchain.pem та certs/privkey.pem відсутні —"
  echo "  поштові сервіси згенерують самопідписаний сертифікат. Для продакшну"
  echo "  покладіть туди сертифікат для MAIL_HOSTNAME (напр. з certbot)."
fi
echo "Готово."
