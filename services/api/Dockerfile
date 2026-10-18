# syntax=docker/dockerfile:1.7
# The AfterLedger API. Build from the repository root (it needs data/ as well as the service):
#   docker build -f services/api/Dockerfile -t afterledger-api .
# The six demo cases are built here, offline with scripted responses, and imported where the API runs (ADR 0009).

FROM python:3.12.15-slim-trixie AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUTF8=1
WORKDIR /app/services/api
COPY services/api/pyproject.toml services/api/uv.lock services/api/README.md ./
# A build behind a TLS-inspecting proxy can pass its CA:  --secret id=proxy_ca,src=ca.crt
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --extra gcp --no-install-project
COPY services/api/src ./src
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    uv sync --frozen --no-dev --extra gcp
COPY data /app/data
RUN AL_DATA_DIR=/app/data AL_STATE_DIR=/tmp/state AL_MODE=offline AL_LLM=scripted AL_STORE=local \
    .venv/bin/afterledger build-demos --out /app/demo_bundles

FROM python:3.12.15-slim-trixie
RUN useradd --create-home --uid 10001 afterledger
WORKDIR /app
COPY --from=build /app/services/api/.venv /app/services/api/.venv
COPY --from=build /app/services/api/src /app/services/api/src
COPY --from=build /app/services/api/pyproject.toml /app/services/api/pyproject.toml
COPY --from=build /app/demo_bundles /app/demo_bundles
COPY data /app/data
ENV PATH=/app/services/api/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 \
    AL_DATA_DIR=/app/data AL_STATE_DIR=/tmp/afterledger AL_DEMO_BUNDLES=/app/demo_bundles \
    PORT=8080
USER afterledger
EXPOSE 8080
# One worker: the service keeps write-through caches and case locks in memory (ADR 0006).
CMD ["sh", "-c", "exec uvicorn afterledger.app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
