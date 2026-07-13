FROM python:3.13-slim

WORKDIR /app

# Install CPU-only torch first, from PyTorch's CPU index, so sentence-transformers
# (pulled in by requirements.txt) reuses it instead of dragging in ~1.5GB of CUDA
# libraries we never use on CPU. Keeps the image lean and CI builds fast.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Then the rest of the dependencies (torch is already satisfied above).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Phase 0: run the healthcheck on startup, then idle so the container stays
# available for `docker compose exec app ...`. Overridden by compose `command`.
CMD ["sh", "-c", "python -m scripts.healthcheck; sleep infinity"]
