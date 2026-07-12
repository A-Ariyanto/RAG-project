FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so they cache across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Phase 0: run the healthcheck on startup, then idle so the container stays
# available for `docker compose exec app ...`. Overridden by compose `command`.
CMD ["sh", "-c", "python -m scripts.healthcheck; sleep infinity"]
