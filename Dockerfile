FROM python:3.13-slim AS base
WORKDIR /app
COPY pyproject.toml README.md ./
COPY frigate_decide ./frigate_decide
RUN pip install --no-cache-dir . \
    && mkdir -p /data && chown nobody:nogroup /data
# /data (stats) is owned by nobody so the compose state volume initializes
# writable by the runtime user.
USER nobody
CMD ["frigate-decide", "run", "--config", "/etc/frigate-decide/config.yaml"]
