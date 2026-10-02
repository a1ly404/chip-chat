# Chip relay — Hyper-V Docker / Linux. Build: docker build -t chip-relay .
FROM python:3.11-slim

WORKDIR /app

# Static Docker CLI only (no daemon). Homelab feed probes use the compose-mounted sock.
ARG DOCKER_CLI_VERSION=27.3.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
    | tar -xzC /usr/local/bin --strip-components=1 docker/docker \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY bin/ bin/
COPY config/ config/
COPY scripts/ scripts/

RUN pip install --no-cache-dir -e '.[relay]' \
    && chmod +x bin/chip bin/chip-relay bin/chip-selftest

ENV PYTHONUNBUFFERED=1 \
    CHIP_CHAT_DATA_DIR=/data/chip-chat

RUN mkdir -p /data/chip-chat

VOLUME ["/data/chip-chat"]

EXPOSE 48082

CMD ["bin/chip-relay", "--listen"]
