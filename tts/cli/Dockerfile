# syntax=docker/dockerfile:1
# Modal CLI image for deploying tts/modal_app.py. Installs from the host uv cache (the `uvcache`
# build context, mounted at its host path because uv's cache entries are absolute symlinks), offline
# first, falling back to PyPI with long timeouts.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /usr/local/bin/uv
ARG HOST_UV_CACHE=/root/.cache/uv
ARG MODAL_VERSION=1.6.1
ENV UV_HTTP_TIMEOUT=600 UV_LINK_MODE=copy UV_SYSTEM_PYTHON=1
RUN --mount=type=bind,from=uvcache,target=${HOST_UV_CACHE},rw \
    export UV_CACHE_DIR=${HOST_UV_CACHE}; \
    uv pip install --offline modal==${MODAL_VERSION} || uv pip install modal==${MODAL_VERSION}
WORKDIR /tts
ENTRYPOINT ["modal"]
