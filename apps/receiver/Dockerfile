FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN python -m pip install --no-cache-dir --timeout 300 \
    --index-url https://pypi.org/simple uv==0.7.6
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev --no-install-project --python 3.12
COPY src /app/src
COPY apps/receiver /app/apps/receiver
RUN uv sync --frozen --no-dev --python 3.12
RUN useradd --system --uid 10001 --create-home appuser

USER appuser

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ["PORT"] + "/health", timeout=2).read()'

CMD ["python", "apps/receiver/start.py"]
