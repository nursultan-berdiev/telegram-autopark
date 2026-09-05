"""Общие фикстуры: env-переменные должны быть заданы ДО импорта app.config."""
from __future__ import annotations

import os

os.environ.setdefault("CORE_API_URL", "http://core-api.test")
os.environ.setdefault("INGEST_TOKEN", "ingest-test-token")
os.environ.setdefault("ADAPTER_TOKEN", "adapter-test-token")
os.environ.setdefault("TRACCAR_URL", "http://traccar.test")
os.environ.setdefault("TRACCAR_USER", "svc@example.com")
os.environ.setdefault("TRACCAR_PASSWORD", "secret")
