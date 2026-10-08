#!/bin/sh
# Конфігурує Postfix через postconf при кожному старті (ідемпотентно).
set -eu

: "${MAIL_HOSTNAME:?MAIL_HOSTNAME is required}"
: "${MAIL_DOMAIN:?MAIL_DOMAIN is required}"
# Лише контейнер backend (фіксована адреса) може відправляти через 10025 без пароля.
# Раніше довірялася вся внутрішня мережа — тобто й ClamAV, Rspamd, Redis тощо.
WEBMAIL_CLIENT_IP="${WEBMAIL_CLIENT_IP:-172.30.0.10}"
DB_PW="$(cat /run/secrets/mail_db_password)"

# ─── TLS ─────────────────────────────────────────────────────
TLS_DIR=/etc/postfix/tls
mkdir -p "$TLS_DIR"
# Пріоритет: сертифікат Let's Encrypt, який отримав Traefik (сервіс certdumper кладе його
# в certs/acme/<MAIL_HOSTNAME>/), далі — вручну покладений certs/fullchain.pem
ACME_DIR="/certs/acme/$MAIL_HOSTNAME"
if [ -s "$ACME_DIR/fullchain.pem" ] && [ -s "$ACME_DIR/privkey.pem" ]; then
  cp "$ACME_DIR/fullchain.pem" "$TLS_DIR/fullchain.pem"
  cp "$ACME_DIR/privkey.pem" "$TLS_DIR/privkey.pem"
  echo "TLS: сертифікат Let's Encrypt для $MAIL_HOSTNAME"
elif [ -s /certs/fullchain.pem ] && [ -s /certs/privkey.pem ]; then
  cp /certs/fullchain.pem "$TLS_DIR/fullchain.pem"
  cp /certs/privkey.pem "$TLS_DIR/privkey.pem"
elif [ ! -s "$TLS_DIR/fullchain.pem" ]; then
  echo "WARN: /certs порожній — генерую самопідписаний сертифікат для $MAIL_HOSTNAME"
  openssl req -x509 -newkey rsa:3072 -nodes -days 825 -subj "/CN=$MAIL_HOSTNAME" \
    -keyout "$TLS_DIR/privkey.pem" -out "$TLS_DIR/fullchain.pem" 2>/dev/null
fi
chmod 600 "$TLS_DIR/privkey.pem"

# ─── SQL-lookup (роль mailreader має доступ лише до представлень) ─
SQL_DIR=/etc/postfix/pgsql
mkdir -p "$SQL_DIR"
write_map() {
  cat > "$SQL_DIR/$1.cf" <<EOM
hosts = postgres
user = mailreader
password = $DB_PW
dbname = blackcloud
query = $2
EOM
}
write_map domains      "SELECT 1 FROM mail_domains_v WHERE domain = lower('%s')"
write_map mailboxes    "SELECT 1 FROM mail_mailboxes_v WHERE address = lower('%s')"
write_map aliases      "SELECT destination FROM mail_aliases_v WHERE source = lower('%s')"
write_map sender_login "SELECT address FROM mail_mailboxes_v WHERE address = lower('%s') UNION SELECT destination FROM mail_aliases_v WHERE source = lower('%s')"
chown root:postfix "$SQL_DIR"/*.cf
chmod 640 "$SQL_DIR"/*.cf

# ─── main.cf ─────────────────────────────────────────────────
postconf -e \
  "myhostname = $MAIL_HOSTNAME" \
  "mydomain = $MAIL_DOMAIN" \
  "myorigin = \$mydomain" \
  "mydestination = localhost" \
  "inet_interfaces = all" \
  "inet_protocols = ipv4" \
  "mynetworks = 127.0.0.0/8" \
  "smtpd_banner = \$myhostname ESMTP" \
  "biff = no" \
  "append_dot_mydomain = no" \
  "compatibility_level = 3.6" \
  "maillog_file = /dev/stdout" \
  "message_size_limit = 26214400" \
  "mailbox_size_limit = 0" \
  "recipient_delimiter = +" \
  "virtual_transport = lmtp:inet:dovecot:24" \
  "virtual_mailbox_domains = pgsql:$SQL_DIR/domains.cf" \
  "virtual_mailbox_maps = pgsql:$SQL_DIR/mailboxes.cf" \
  "virtual_alias_maps = pgsql:$SQL_DIR/aliases.cf" \
  "smtpd_sender_login_maps = pgsql:$SQL_DIR/sender_login.cf" \
  "smtpd_tls_cert_file = $TLS_DIR/fullchain.pem" \
  "smtpd_tls_key_file = $TLS_DIR/privkey.pem" \
  "smtpd_tls_security_level = may" \
  "smtpd_tls_auth_only = yes" \
  "smtpd_tls_protocols = >=TLSv1.2" \
  "smtpd_tls_mandatory_protocols = >=TLSv1.2" \
  "smtpd_tls_mandatory_ciphers = high" \
  "smtpd_tls_loglevel = 1" \
  "smtpd_tls_received_header = yes" \
  "smtp_tls_security_level = may" \
  "smtp_tls_protocols = >=TLSv1.2" \
  "smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt" \
  "smtp_tls_loglevel = 1" \
  "tls_preempt_cipherlist = yes" \
  "smtpd_sasl_type = dovecot" \
  "smtpd_sasl_path = inet:dovecot:12345" \
  "smtpd_sasl_auth_enable = no" \
  "smtpd_sasl_security_options = noanonymous, noplaintext" \
  "smtpd_sasl_tls_security_options = noanonymous" \
  "broken_sasl_auth_clients = no" \
  "smtpd_helo_required = yes" \
  "disable_vrfy_command = yes" \
  "strict_rfc821_envelopes = yes" \
  "smtpd_delay_reject = yes" \
  "smtpd_helo_restrictions = permit_mynetworks, reject_invalid_helo_hostname, reject_non_fqdn_helo_hostname" \
  "smtpd_sender_restrictions = permit_mynetworks, reject_non_fqdn_sender, reject_unknown_sender_domain" \
  "smtpd_relay_restrictions = permit_mynetworks, permit_sasl_authenticated, reject_unauth_destination" \
  "smtpd_recipient_restrictions = reject_non_fqdn_recipient, reject_unknown_recipient_domain, reject_unlisted_recipient" \
  "smtpd_data_restrictions = reject_unauth_pipelining" \
  "smtpd_client_connection_rate_limit = 30" \
  "smtpd_client_message_rate_limit = 60" \
  "anvil_rate_time_unit = 60s" \
  "smtpd_milters = inet:rspamd:11332" \
  "non_smtpd_milters = inet:rspamd:11332" \
  "milter_default_action = accept" \
  "milter_protocol = 6" \
  "milter_mail_macros = i {mail_addr} {client_addr} {client_name} {auth_authen}"

# Вимикаємо chroot (контейнер і так ізольований; спрощує DNS/TLS)
postconf -F '*/*/chroot = n'

# ─── master.cf: submission 587 (STARTTLS), 465 (implicit TLS), webmail 10025 ─
SUBMISSION_OPTS="smtpd_tls_security_level=encrypt
smtpd_sasl_auth_enable=yes
smtpd_tls_auth_only=yes
smtpd_client_restrictions=permit_sasl_authenticated,reject
smtpd_sender_restrictions=reject_sender_login_mismatch,reject_non_fqdn_sender
smtpd_relay_restrictions=permit_sasl_authenticated,reject
smtpd_recipient_restrictions=permit_sasl_authenticated,reject
milter_macro_daemon_name=ORIGINATING"

# Числові порти: у slim-образі може не бути /etc/services
postconf -M "587/inet=587 inet n - n - - smtpd"
postconf -M "465/inet=465 inet n - n - - smtpd"
for svc in 587 465; do
  postconf -P "$svc/inet/syslog_name=postfix/submission-$svc"
  echo "$SUBMISSION_OPTS" | while IFS= read -r opt; do
    postconf -P "$svc/inet/$opt"
  done
done
postconf -P "465/inet/smtpd_tls_wrappermode=yes"

# Внутрішній порт для веб-пошти Django: приймає лише з адреси контейнера backend.
# Відповідність From ↔ скринька перевіряє Django.
postconf -M "10025/inet=10025 inet n - n - - smtpd"
for opt in \
  "syslog_name=postfix/webmail" \
  "mynetworks=127.0.0.0/8,$WEBMAIL_CLIENT_IP/32" \
  "smtpd_client_restrictions=permit_mynetworks,reject" \
  "smtpd_relay_restrictions=permit_mynetworks,reject" \
  "smtpd_recipient_restrictions=permit_mynetworks,reject" \
  "smtpd_sasl_auth_enable=no" \
  "smtpd_tls_security_level=none" \
  "smtpd_tls_auth_only=no" \
  "milter_macro_daemon_name=ORIGINATING"; do
  postconf -P "10025/inet/$opt"
done

# DNS для Postfix всередині контейнера
cp /etc/resolv.conf /var/spool/postfix/etc/resolv.conf 2>/dev/null || true

postfix check
exec postfix start-fg
