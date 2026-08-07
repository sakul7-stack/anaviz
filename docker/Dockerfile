# anaviz project container — TimescaleDB + Python app for both services.
# `docker-compose.yml` overrides `command` per service.
FROM timescale/timescaledb:latest-pg16

RUN apk update \
 && apk add --no-cache python3 py3-pip curl bash \
 && rm -rf /var/cache/apk/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY server ./server
COPY frontend ./frontend
RUN pip install --no-cache-dir . \
 || pip install --break-system-packages --no-cache-dir .

COPY docker/entrypoint.sh /usr/local/bin/anaviz-entrypoint
RUN chmod +x /usr/local/bin/anaviz-entrypoint

ENV PATH="/usr/local/bin:${PATH}"
ENTRYPOINT ["/usr/local/bin/anaviz-entrypoint"]
EXPOSE 8000 9000
