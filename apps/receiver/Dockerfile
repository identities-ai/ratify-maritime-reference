FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_HTTP_TIMEOUT=300 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN python -m pip install --no-cache-dir --timeout 300 \
    --index-url https://pypi.org/simple uv==0.7.6
COPY . /app
RUN uv sync --frozen --no-dev --python 3.12

CMD ["python", "apps/receiver/start.py"]
