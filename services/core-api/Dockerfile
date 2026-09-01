# Контекст сборки — КОРЕНЬ репозитория (нужен libs/contracts), см. plan/01.
FROM python:3.12-slim

WORKDIR /app

COPY libs/contracts /libs/contracts
RUN pip install --no-cache-dir /libs/contracts

COPY services/core-api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY services/core-api/alembic.ini ./
COPY services/core-api/alembic/ ./alembic/
COPY services/core-api/app/ ./app/

RUN useradd -m -u 1000 api && mkdir -p /app/storage && chown -R api:api /app
USER api

ENV PYTHONUNBUFFERED=1 \
    FILES_DIR=/app/storage

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
