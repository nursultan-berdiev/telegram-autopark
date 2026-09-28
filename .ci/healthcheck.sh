#!/usr/bin/env bash
# Проверка живости стека после деплоя. ТОЛЬКО чтение — БД и тома не трогает.
# Запускать из каталога проекта (/opt/fleet). Ненулевой код => деплой считается неуспешным.
set -uo pipefail

# Ожидаемые контейнеры (имена фиксированы в docker-compose.yml).
CONTAINERS="fleet_pg fleet_redis fleet_core_api fleet_tracker_adapter fleet_bot fleet_celery_worker fleet_celery_beat"
fail=0

echo "== docker compose ps =="
docker compose ps 2>&1 || true

echo "== статусы контейнеров =="
for c in $CONTAINERS; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  echo "  $c: $st"
  [ "$st" = "running" ] || { echo "  !! $c не running"; fail=1; }
done

# core-api: /health отвечает 200 (на старте гоняет alembic upgrade head, ждём до ~90с).
echo "== core-api /health =="
ok=0
for _ in $(seq 1 45); do
  code=$(docker exec fleet_core_api python -c \
    "import urllib.request as u;print(u.urlopen('http://localhost:8000/health',timeout=3).status)" 2>/dev/null || echo 000)
  if [ "$code" = "200" ]; then ok=1; break; fi
  sleep 2
done
[ "$ok" = 1 ] && echo "  core-api /health: 200" || { echo "  !! core-api /health не отвечает"; fail=1; }

# tracker-adapter: /health => traccar:true (WS к Traccar жив) и ingest:true (воркер шлёт в core-api).
echo "== tracker-adapter /health =="
ok=0
for _ in $(seq 1 30); do
  out=$(docker exec fleet_tracker_adapter python -c \
    "import urllib.request as u;print(u.urlopen('http://localhost:8001/health',timeout=3).read().decode())" 2>/dev/null || echo '{}')
  if echo "$out" | grep -q '"traccar": *true' && echo "$out" | grep -q '"ingest": *true'; then
    ok=1; echo "  adapter /health: $out"; break
  fi
  sleep 2
done
[ "$ok" = 1 ] || { echo "  !! adapter /health без traccar+ingest=true"; fail=1; }

if [ "$fail" = 0 ]; then echo "HEALTHCHECK OK"; else echo "HEALTHCHECK FAILED"; fi
exit "$fail"
