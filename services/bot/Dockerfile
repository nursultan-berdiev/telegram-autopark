# Контекст сборки — КОРЕНЬ репозитория (нужен libs/contracts), см. plan/01.
# ИИ и БД в боте нет: он только Telegram-клиент к core-api.
FROM python:3.12-slim

WORKDIR /app

COPY libs/contracts /libs/contracts
RUN pip install --no-cache-dir /libs/contracts

COPY services/bot/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY services/bot/app/ ./app/

RUN useradd -m -u 1000 bot && chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "app"]
