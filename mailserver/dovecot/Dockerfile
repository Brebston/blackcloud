# Debian bookworm = Dovecot 2.3 (синтаксис конфігурації нижче саме для 2.3)
FROM debian:bookworm-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      dovecot-core dovecot-imapd dovecot-lmtpd dovecot-pgsql dovecot-sieve ca-certificates openssl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 5000 vmail && useradd -u 5000 -g vmail -d /var/mail/vhosts -s /usr/sbin/nologin vmail

COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY spam-to-junk.sieve /etc/dovecot/sieve/spam-to-junk.sieve
RUN sievec /etc/dovecot/sieve/spam-to-junk.sieve
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 993 143 24 12345
ENTRYPOINT ["/entrypoint.sh"]
