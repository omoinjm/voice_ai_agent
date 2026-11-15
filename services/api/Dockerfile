# syntax=docker/dockerfile:1
FROM python:3.12-slim
# Same uv version and host-cache trick as services/agent/Dockerfile (see the comments there).
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
ARG HOST_UV_CACHE=/root/.cache/uv
# INSTALL_DEV=1 adds pytest and ruff (local compose and CI); the deployed image leaves them out.
ARG INSTALL_DEV=0
ENV UV_HTTP_TIMEOUT=600 UV_CONCURRENT_DOWNLOADS=4 UV_LINK_MODE=copy
RUN --mount=type=bind,from=uvcache,target=${HOST_UV_CACHE},rw \
    export UV_CACHE_DIR=${HOST_UV_CACHE}; \
    if [ "$INSTALL_DEV" = 1 ]; then DEV=""; else DEV="--no-dev"; fi; \
    uv export --frozen $DEV --no-hashes --no-emit-project -o /tmp/requirements.txt \
    && uv venv && uv pip install --offline -r /tmp/requirements.txt && exit 0; \
    for i in 1 2 3 4 5; do uv sync --frozen $DEV && exit 0; echo "uv sync attempt $i failed, retrying"; sleep 5; done; exit 1
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
