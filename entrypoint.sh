#!/usr/bin/env bash
set -e

PORT="${PORT:-7860}"

echo "Starting Yekis Intelligence Cloud Stack..."

cleanup() {
    echo "Stopping background services..."
    kill $(jobs -p) 2>/dev/null || true
    if command -v pg_ctl >/dev/null 2>&1; then
        pg_ctl -D /app/var/pgdata stop -m fast 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

# 1. Start Redis if local
if [[ -z "$REDIS_URL" || "$REDIS_URL" == *"localhost"* || "$REDIS_URL" == *"127.0.0.1"* ]]; then
    echo "Starting local Redis daemon..."
    redis-server --daemonize yes
    export REDIS_URL="redis://127.0.0.1:6379/0"
fi

# 2. Start PostgreSQL if local
if [[ -z "$DATABASE_URL" || "$DATABASE_URL" == *"localhost"* || "$DATABASE_URL" == *"127.0.0.1"* ]]; then
    echo "Configuring and starting local PostgreSQL..."
    PGDATA="/app/var/pgdata"
    export PGDATA
    if [ ! -f "$PGDATA/PG_VERSION" ]; then
        echo "Initializing fresh PostgreSQL database cluster..."
        initdb -D "$PGDATA" -U yekis_intel
        pg_ctl -D "$PGDATA" -l /app/var/postgres.log -o "-p 5432" start
        createdb -U yekis_intel -h 127.0.0.1 yekis_intelligence || true
    else
        echo "Starting existing PostgreSQL cluster..."
        pg_ctl -D "$PGDATA" -l /app/var/postgres.log -o "-p 5432" start || true
    fi
    export DATABASE_URL="postgresql+psycopg://yekis_intel@127.0.0.1:5432/yekis_intelligence"
fi

# 3. Wait for database readiness
echo "Waiting for database..."
for i in {1..15}; do
    if python -c "from app.db import engine; engine.connect()" 2>/dev/null; then
        echo "Database is ready."
        break
    fi
    sleep 1
done

# 4. Run database migrations
echo "Applying database schema migrations..."
alembic upgrade head || true

# 5. Seed sources
echo "Checking seed sources..."
python -m scripts.seed_sources || true

# 6. Start Celery worker in background
echo "Starting Celery worker..."
celery -A app.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=1 &

# 7. Start Celery beat scheduler in background
echo "Starting Celery beat..."
celery -A app.worker.celery_app:celery_app beat --loglevel=INFO &

# 8. Start FastAPI web server on target port
echo "Starting FastAPI on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
