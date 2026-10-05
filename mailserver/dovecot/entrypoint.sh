#!/bin/sh
set -eu

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
# Master-user веб-пошти приймається лише з адреси контейнера backend
WEBMAIL_CLIENT_IP="${WEBMAIL_CLIENT_IP:-172.30.0.10}"
DB_PW="$(cat /run/secrets/mail_db_password)"
MASTER_PW="$(cat /run/secrets/dovecot_master_password)"

# ─── TLS ───
TLS_DIR=/etc/dovecot/tls
mkdir -p "$TLS_DIR"
if [ -s /certs/fullchain.pem ] && [ -s /certs/privkey.pem ]; then
  cp /certs/fullchain.pem "$TLS_DIR/fullchain.pem"
  cp /certs/privkey.pem "$TLS_DIR/privkey.pem"
elif [ ! -s "$TLS_DIR/fullchain.pem" ]; then
  echo "WARN: /certs порожній — генерую самопідписаний сертифікат для $MAIL_HOSTNAME"
  openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=$MAIL_HOSTNAME" \
    -keyout "$TLS_DIR/privkey.pem" -out "$TLS_DIR/fullchain.pem" 2>/dev/null
fi
chmod 600 "$TLS_DIR/privkey.pem"

# ─── SQL ───
cat > /etc/dovecot/dovecot-sql.conf.ext <<EOM
driver = pgsql
connect = host=postgres dbname=blackcloud user=mailreader password=$DB_PW
default_pass_scheme = ARGON2ID
password_query = SELECT address AS user, password FROM mail_mailboxes_v WHERE address = lower('%u') AND password IS NOT NULL
user_query = SELECT '/var/mail/vhosts/' || domain || '/' || local_part AS home, 5000 AS uid, 5000 AS gid, '*:storage=' || quota_mb || 'M' AS quota_rule FROM mail_mailboxes_v WHERE address = lower('%u')
iterate_query = SELECT address AS user FROM mail_mailboxes_v
EOM
chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext
chmod 640 /etc/dovecot/dovecot-sql.conf.ext

# ─── Master-user для веб-пошти (лише з внутрішньої мережі) ───
HASH="$(doveadm pw -s ARGON2ID -p "$MASTER_PW")"
printf 'webmail:%s::::::allow_nets=%s\n' "$HASH" "$WEBMAIL_CLIENT_IP/32" > /etc/dovecot/master-users
chown root:dovecot /etc/dovecot/master-users
chmod 640 /etc/dovecot/master-users

# ─── Шифрування листів на диску ───
# Нові листи шифруються глобальним публічним ключем; старі (незашифровані) читаються як раніше.
if [ -s /run/secrets/mail_crypt_private_key ] && [ -s /run/secrets/mail_crypt_public_key ]; then
  cat > /etc/dovecot/mail-crypt.conf <<'EOM'
mail_plugins = $mail_plugins mail_crypt
plugin {
  mail_crypt_global_private_key = </run/secrets/mail_crypt_private_key
  mail_crypt_global_public_key = </run/secrets/mail_crypt_public_key
  mail_crypt_save_version = 2
}
EOM
  echo "mail_crypt: шифрування листів на диску увімкнено"
else
  rm -f /etc/dovecot/mail-crypt.conf
  echo "WARN: mail_crypt вимкнено — немає ключів у secrets/ (make secrets)"
fi

mkdir -p /var/mail/vhosts
chown -R vmail:vmail /var/mail/vhosts

exec dovecot -F
