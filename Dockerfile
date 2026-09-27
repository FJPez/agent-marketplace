FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --locked --no-dev --no-install-project

COPY app ./app

RUN uv sync --locked --no-dev

FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# uvicorn takes the client address from X-Forwarded-For only when the connection
# comes from FORWARDED_ALLOW_IPS. The default trusts only loopback, so a published
# container ignores forged headers; behind a proxy that overwrites the header (Railway),
# set it to that proxy's addresses or "*".
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini ./

EXPOSE 8000

# exec makes uvicorn PID 1, so it receives the SIGTERM from `docker stop` and Railway.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
