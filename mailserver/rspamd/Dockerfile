ARG DEBIAN_IMAGE=debian:bookworm-slim
FROM ${DEBIAN_IMAGE}

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends rspamd ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY local.d/ /etc/rspamd/local.d/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11332
ENTRYPOINT ["/entrypoint.sh"]
