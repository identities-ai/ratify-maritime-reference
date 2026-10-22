FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 \
    CARGO_HOME=/opt/cargo \
    RUSTUP_HOME=/opt/rustup \
    PATH="/opt/cargo/bin:$PATH"

WORKDIR /app
# RATIFY_V2_PIN: Protocol v2 is not on PyPI yet, so its native signing
# extension builds from the source exported by scripts/vendor_ratify_v2.sh.
# That is the only reason this stage carries a Rust toolchain and vendor/.
# Once ratify-protocol-v2 and ratify-protocol-native-v2 2.0.0b1 are published
# and pinned, drop the toolchain, the vendor COPY and this comment.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --timeout 300 \
        --index-url https://pypi.org/simple uv==0.7.6 \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --profile minimal --default-toolchain 1.95.0
COPY vendor/ratify-protocol /app/vendor/ratify-protocol
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --python 3.12
COPY src /app/src
COPY apps/agent /app/apps/agent
RUN uv sync --frozen --no-dev --python 3.12

FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY --from=build /app/apps/agent /app/apps/agent
# No authority material is baked in. Both runtimes read their delegation and
# adversarial fixture from the persistent volume, written through Maritime's
# file API, and verify each against a digest supplied in the environment. That
# is what lets a rotation be a write plus a restart instead of an image
# rebuild, and it is what gives the digest check something that can actually
# change to guard.
RUN useradd --system --uid 10001 --create-home appuser

USER appuser

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ["PORT"] + "/health", timeout=2).read()'

CMD ["/app/.venv/bin/python", "apps/agent/start.py"]
