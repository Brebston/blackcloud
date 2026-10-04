# Власний образ ClamAV: офіційний clamav/clamav не має збірки для arm64 (Apple Silicon)
FROM debian:bookworm-slim

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      clamav clamav-daemon clamav-freshclam ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/clamav && chown clamav:clamav /run/clamav /var/lib/clamav

COPY clamd.conf /etc/clamav/clamd.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod 644 /etc/clamav/clamd.conf && chmod 755 /entrypoint.sh

EXPOSE 3310
ENTRYPOINT ["/entrypoint.sh"]
