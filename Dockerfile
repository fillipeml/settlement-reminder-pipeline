# Container image for the daily routine (run it from a scheduler with the state on a volume).
#   docker build -t settlement-reminders .
#   docker run --rm --env-file .env -v $(pwd)/data:/app/data settlement-reminders
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

COPY fixtures ./fixtures
ENV PATH="/app/.venv/bin:$PATH" HISTORY_DB=/app/data/history.sqlite
VOLUME ["/app/data"]

CMD ["settlement-reminders"]
