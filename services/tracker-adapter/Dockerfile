# Контекст сборки — КОРЕНЬ репозитория (нужен libs/contracts), см. plan/01.
FROM python:3.12-slim

WORKDIR /app

COPY libs/contracts /libs/contracts
RUN pip install --no-cache-dir /libs/contracts

COPY services/tracker-adapter/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY services/tracker-adapter/app/ ./app/

RUN useradd -m -u 1000 adapter && chown -R adapter:adapter /app
USER adapter

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
