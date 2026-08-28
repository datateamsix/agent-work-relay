FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

WORKDIR /app

RUN useradd --system --uid 1001 --create-home awr

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --extra hosted --frozen --no-dev --no-editable \
    && chown -R awr:awr /app

USER awr
ENV PATH="/app/.venv/bin:$PATH" \
    AWR_ENV=production \
    AWR_AUTH_MODE=oauth \
    AWR_STORAGE=firestore \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "uvicorn awr.transports.asgi:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}"]
