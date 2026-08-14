FROM debian:trixie-slim

LABEL org.opencontainers.image.title="ThinClient PXE server" \
      org.opencontainers.image.description="TFTP and HTTP services for ThinClient diskless PXE boot" \
      org.opencontainers.image.source="https://github.com/patnawa/Thinclient"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        python3 \
        tftpd-hpa \
    && rm -rf /var/lib/apt/lists/*

COPY tools/tc-config-server.py /usr/local/lib/thinclient/tc-config-server.py
COPY deploy/docker-pxe/container-entrypoint.sh /usr/local/bin/thinclient-pxe-entrypoint

RUN chmod 0555 \
        /usr/local/lib/thinclient/tc-config-server.py \
        /usr/local/bin/thinclient-pxe-entrypoint

EXPOSE 69/udp 8080/tcp

ENTRYPOINT ["/usr/local/bin/thinclient-pxe-entrypoint"]
