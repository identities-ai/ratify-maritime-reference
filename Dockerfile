FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 \
    RATIFY_DELEGATION_PATH=/app/deployment/delegation.json \
    RATIFY_SCENARIO_AUTHORITIES_PATH=/app/deployment/scenario-authorities.json \
    PATH="/app/.venv/bin:$PATH"

ARG RATIFY_DELEGATION_SHA256
ARG RATIFY_SCENARIO_AUTHORITIES_SHA256
ARG RATIFY_SECOND_DELEGATION_SHA256
ARG RATIFY_PEER_AUTHORITIES_SHA256

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --timeout 300 \
        --index-url https://pypi.org/simple uv==0.7.6
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --python 3.12
COPY src /app/src
COPY apps/agent /app/apps/agent
# One image serves both runtimes. It carries every tenant's public certificate,
# because Maritime's runtime configuration cannot carry a hybrid certificate of
# this size. Each runtime is pointed at its own files by environment and
# receives only its own private key, which never enters the image.
RUN --mount=type=secret,id=ratify_delegation_b64 \
    --mount=type=secret,id=ratify_scenario_authorities_gzip_b64 \
    --mount=type=secret,id=ratify_delegation_b_b64 \
    --mount=type=secret,id=ratify_peer_authorities_gzip_b64 \
    set -eu; \
    if [ -s /run/secrets/ratify_delegation_b64 ] && [ -s /run/secrets/ratify_scenario_authorities_gzip_b64 ]; then \
        test -n "$RATIFY_DELEGATION_SHA256"; \
        test -n "$RATIFY_SCENARIO_AUTHORITIES_SHA256"; \
        test -n "$RATIFY_SECOND_DELEGATION_SHA256"; \
        test -n "$RATIFY_PEER_AUTHORITIES_SHA256"; \
        mkdir -p /app/deployment; \
        base64 --decode /run/secrets/ratify_delegation_b64 > /app/deployment/delegation.json; \
        base64 --decode /run/secrets/ratify_scenario_authorities_gzip_b64 | gzip --decompress > /app/deployment/scenario-authorities.json; \
        base64 --decode /run/secrets/ratify_delegation_b_b64 > /app/deployment/delegation-b.json; \
        base64 --decode /run/secrets/ratify_peer_authorities_gzip_b64 | gzip --decompress > /app/deployment/scenario-authorities-b.json; \
        test "$(sha256sum /app/deployment/delegation.json | cut -d ' ' -f 1)" = "$RATIFY_DELEGATION_SHA256"; \
        test "$(sha256sum /app/deployment/scenario-authorities.json | cut -d ' ' -f 1)" = "$RATIFY_SCENARIO_AUTHORITIES_SHA256"; \
        test "$(sha256sum /app/deployment/delegation-b.json | cut -d ' ' -f 1)" = "$RATIFY_SECOND_DELEGATION_SHA256"; \
        test "$(sha256sum /app/deployment/scenario-authorities-b.json | cut -d ' ' -f 1)" = "$RATIFY_PEER_AUTHORITIES_SHA256"; \
        chmod 0444 /app/deployment/delegation.json /app/deployment/scenario-authorities.json /app/deployment/delegation-b.json /app/deployment/scenario-authorities-b.json; \
    fi
RUN uv sync --frozen --no-dev --python 3.12
RUN useradd --system --uid 10001 --create-home appuser

USER appuser

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ["PORT"] + "/health", timeout=2).read()'

CMD ["/app/.venv/bin/python", "apps/agent/start.py"]
