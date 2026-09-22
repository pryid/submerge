FROM docker.io/library/python:3.12-alpine@sha256:4c47124a8391cb7a9f571164147d154777cf012a4ece5f86097130d7a4478111 AS python

FROM python AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc /uv /usr/local/bin/uv
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache --python /usr/local/bin/python

FROM python AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SUB_BASES_FILE=/config/sub_bases.json \
    SUB_METADATA_FILE=/config/sub_metadata.json

COPY --from=dependencies /opt/venv /opt/venv
COPY submerge/ ./submerge/
ARG BUILD_REVISION=""
ENV BUILD_REVISION=${BUILD_REVISION}
USER 65532:65532
EXPOSE 18080
CMD ["python", "-m", "submerge"]
