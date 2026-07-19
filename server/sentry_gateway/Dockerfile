# Sentry Gateway. Runs as a non-root user inside the container.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so application edits do not invalidate the layer.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "pydantic>=2.9" \
        "pydantic-settings>=2.6" \
        "email-validator>=2.2" \
        "asyncpg>=0.30" \
        "pyjwt[crypto]>=2.9" \
        "argon2-cffi>=23.1" \
        "httpx>=0.27"

COPY app ./app
COPY migrations ./migrations
# Operator tooling: bootstrapping the first device is an out-of-band step, so it
# ships with the image rather than existing as an unauthenticated endpoint.
COPY scripts ./scripts

# Never run the control plane as root.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin sentry \
    && chown -R sentry:sentry /app
USER sentry

EXPOSE 8090

# Health is checked by the orchestrator against the liveness endpoint, which
# deliberately does not touch PostgreSQL or the runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health/live', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
