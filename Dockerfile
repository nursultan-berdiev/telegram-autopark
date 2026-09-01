# Бот не содержит ни claude, ни Node: ИИ живёт в claude-gateway, бот ходит к нему
# по HTTP (AI_BACKEND=http). Поэтому образ — обычный тонкий Python.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY bot/ ./bot/

RUN useradd -m -u 1000 bot && mkdir -p /app/storage && chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1 \
    FILES_DIR=/app/storage

# Миграции применяются на старте — контейнер сам приводит схему к head.
CMD ["sh", "-c", "alembic upgrade head && python -m bot"]
