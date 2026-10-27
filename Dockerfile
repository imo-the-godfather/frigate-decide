FROM python:3.13-slim AS base
WORKDIR /app
COPY pyproject.toml README.md ./
COPY frigate_decide ./frigate_decide
RUN pip install --no-cache-dir .
USER nobody
CMD ["frigate-decide", "run", "--config", "/etc/frigate-decide/config.yaml"]
