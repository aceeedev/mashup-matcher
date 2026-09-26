# Built with the repo root as context (see compose.yml) so this can copy in core/
# alongside backend/. Both are installed as real packages via pip (see their
# pyproject.toml), so models/providers/services/utils import exactly as they do
# on the host - no PYTHONPATH hacks needed.
FROM python:3.13-slim

WORKDIR /app
COPY core/ core/
COPY backend/ backend/

RUN pip install --no-cache-dir ./core ./backend

WORKDIR /app/backend
EXPOSE 5000

# Overridden by the worker service in compose.yml to run `rq worker` instead.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "app:app"]
