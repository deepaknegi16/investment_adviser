FROM python:3.12-slim

# curl is for the container healthcheck; build tools are not needed because
# every wheel in requirements.txt ships a manylinux build.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first so a code change does not invalidate the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY *.py *.json ./

# adviser.db lives on a volume, not in the image — see compose.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Run as a non-root user; the volume is chowned to match.
# Prometheus multiprocess counters need a shared, writable directory: each
# uvicorn worker is its own process and writes its own counter file there.
RUN mkdir -p /tmp/prom && useradd -u 10001 -m adviser \
    && chown -R adviser:adviser /app /tmp/prom
USER adviser

HEALTHCHECK --interval=20s --timeout=4s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1   # not localhost: see compose

EXPOSE 8000
# --workers is what makes the shared cache matter: each worker is a separate
# process with its own memory, so without Redis they would each re-fetch
# everything the others already have.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "3"]
