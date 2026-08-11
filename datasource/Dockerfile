# anaviz DATASOURCE image — the independent HLT data API.
# Build context is ./datasource — this image contains ONLY the HLT datasource
# package (anaviz_datasource). It has no knowledge of the project at all.
FROM timescale/timescaledb:latest-pg16

RUN apk update \
 && apk add --no-cache python3 py3-pip curl bash \
 && rm -rf /var/cache/apk/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY anaviz_datasource ./anaviz_datasource
COPY schema.sql ./schema.sql
RUN pip install --no-cache-dir . \
 || pip install --break-system-packages --no-cache-dir .

COPY entrypoint.sh /usr/local/bin/anaviz-entrypoint
RUN chmod +x /usr/local/bin/anaviz-entrypoint

ENV PATH="/usr/local/bin:${PATH}"
ENTRYPOINT ["/usr/local/bin/anaviz-entrypoint"]
EXPOSE 9000
