# syntax=docker/dockerfile:1
FROM python:3.12-slim
# Pinned to the host's uv version so the host uv cache (the `uvcache` build context) is readable.
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# PyPI downloads are slow and flaky here, so reuse the host uv cache (~/.cache/uv, passed in as the
# `uvcache` build context; writes during the build are discarded). It's mounted at its host path
# because uv's cache entries are absolute symlinks. Install the locked versions offline first with
# `uv pip install` (`uv sync` keys locked wheels differently and misses the host cache); if something
# isn't cached, fall back to an online `uv sync` with long timeouts and retries.
ARG HOST_UV_CACHE=/root/.cache/uv
ENV UV_HTTP_TIMEOUT=600 UV_CONCURRENT_DOWNLOADS=4 UV_LINK_MODE=copy
RUN --mount=type=bind,from=uvcache,target=${HOST_UV_CACHE},rw \
    export UV_CACHE_DIR=${HOST_UV_CACHE}; \
    uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt \
    && uv venv && uv pip install --offline -r /tmp/requirements.txt && exit 0; \
    for i in 1 2 3 4 5; do uv sync --frozen --no-dev && exit 0; echo "uv sync attempt $i failed, retrying"; sleep 5; done; exit 1
COPY *.py ./
ENV SHARED_DIR=/shared PATH="/app/.venv/bin:$PATH"
EXPOSE 7860
# Add --whatsapp (and WHATSAPP_* env) to also answer WhatsApp Business calls.
CMD ["python", "bot.py", "-t", "webrtc", "--host", "0.0.0.0", "--port", "7860"]
