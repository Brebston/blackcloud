#!/bin/sh
# Окрема роль для Postfix/Dovecot: лише SELECT з поштових представлень.
# Права на самі представлення видає Django після міграцій (apps/mail/sql.py).
set -eu
MAIL_PW="$(cat /run/secrets/mail_db_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v mailpw="$MAIL_PW" -v db="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE mailreader LOGIN PASSWORD :'mailpw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO mailreader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO mailreader;
EOSQL
