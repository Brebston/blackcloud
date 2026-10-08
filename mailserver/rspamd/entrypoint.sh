#!/bin/sh
set -eu
: "${MAIL_DOMAIN:?MAIL_DOMAIN is required}"
REDIS_PW="$(cat /run/secrets/redis_password)"
WEBMAIL_CLIENT_IP="${WEBMAIL_CLIENT_IP:-172.30.0.10}"

# «Локальний» відправник (підпис DKIM без автентифікації) — лише Postfix-loopback і backend
cat > /etc/rspamd/local.d/options.inc <<EOM
local_addrs = [127.0.0.0/8, $WEBMAIL_CLIENT_IP/32];
EOM

cat > /etc/rspamd/local.d/redis.conf <<EOM
servers = "redis:6379";
password = "$REDIS_PW";
db = "3";
EOM
chmod 640 /etc/rspamd/local.d/redis.conf
chown root:_rspamd /etc/rspamd/local.d/redis.conf

# Spamhaus через DQS: публічні DNS-резолвери (і резолвери провайдера) Spamhaus блокує,
# тож без ключа перевірки zen/dbl не працюють. Безкоштовний ключ: spamhaus.com → Data Query Service
if [ -n "${SPAMHAUS_DQS_KEY:-}" ]; then
  case "$SPAMHAUS_DQS_KEY" in
    *[!a-z0-9]*) echo "ERROR: SPAMHAUS_DQS_KEY має містити лише a-z і 0-9" >&2; exit 1 ;;
  esac
  cat > /etc/rspamd/local.d/rbl.conf <<EOM
rbls {
  spamhaus {
    rbl = "$SPAMHAUS_DQS_KEY.zen.dq.spamhaus.net";
  }
  "DBL" {
    rbl = "$SPAMHAUS_DQS_KEY.dbl.dq.spamhaus.net";
  }
}
EOM
  chmod 640 /etc/rspamd/local.d/rbl.conf
  chown root:_rspamd /etc/rspamd/local.d/rbl.conf
  echo "Spamhaus DQS увімкнено"
else
  rm -f /etc/rspamd/local.d/rbl.conf
  echo "WARN: SPAMHAUS_DQS_KEY не задано — перевірки Spamhaus, ймовірно, не працюватимуть"
fi

# DKIM-ключ генерується один раз і зберігається у томі
DKIM_DIR=/var/lib/rspamd/dkim
mkdir -p "$DKIM_DIR"
KEY="$DKIM_DIR/$MAIL_DOMAIN.mail.key"
if [ ! -s "$KEY" ]; then
  rspamadm dkim_keygen -b 2048 -s mail -d "$MAIL_DOMAIN" -k "$KEY" > "$DKIM_DIR/$MAIL_DOMAIN.mail.txt"
  echo "Згенеровано DKIM-ключ. DNS-запис (make dkim):"
  cat "$DKIM_DIR/$MAIL_DOMAIN.mail.txt"
fi
chown -R _rspamd:_rspamd /var/lib/rspamd
chmod 600 "$KEY"

rspamadm configtest
exec rspamd -f -u _rspamd -g _rspamd
