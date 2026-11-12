FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/usr/lib/postgresql/16/bin:/usr/lib/postgresql/15/bin:${PATH}"
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql \
    postgresql-contrib \
    redis-server \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY entrypoint.sh ./entrypoint.sh

RUN pip install --no-cache-dir . && \
    useradd -m -u 1000 appuser && \
    mkdir -p /app/var /app/var/pgdata && \
    chmod +x ./entrypoint.sh && \
    chown -R appuser:appuser /app

USER appuser
EXPOSE 7860
CMD ["./entrypoint.sh"]
