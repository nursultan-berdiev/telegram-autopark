#!/usr/bin/env bash
# Безопасный деплой стека fleet на сервере. Запускать из /opt/fleet (код уже
# синхронизирован туда). Гарантии:
#  - сборка ДО подъёма: упавший билд не трогает рабочие контейнеры;
#  - никаких down / down -v / volume prune — том БД неприкосновенен;
#  - откат на прежние образы при неуспешном health-check;
#  - чистка только висячих образов и кэша сборки (без -a / --volumes).
set -euo pipefail

: "${COMPOSE_PROJECT_NAME:=fleet}"        # строго существующий проект и его тома
export COMPOSE_PROJECT_NAME
PREFIX="${COMPOSE_PROJECT_NAME}-"

# service -> container_name (имена зафиксированы в docker-compose.yml)
SERVICES="core-api tracker-adapter bot celery-worker celery-beat"
container_of() { case "$1" in
  core-api) echo fleet_core_api;; tracker-adapter) echo fleet_tracker_adapter;;
  bot) echo fleet_bot;; celery-worker) echo fleet_celery_worker;; celery-beat) echo fleet_celery_beat;;
esac; }

echo "### 1/5 снимок текущих образов для отката (best-effort, не роняет деплой)"
have_rollback=""
for s in $SERVICES; do
  old=$(docker inspect -f '{{.Image}}' "$(container_of "$s")" 2>/dev/null || true)
  # Тегируем только реальный образ: у сервисов с общим Dockerfile тег бывает
  # перетёрт (image ref «фантомный») — такой пропускаем, а не падаем.
  if [ -n "$old" ] && docker image inspect "$old" >/dev/null 2>&1 \
       && docker tag "$old" "${PREFIX}${s}:rollback" 2>/dev/null; then
    have_rollback="yes"
    echo "  ${PREFIX}${s}:rollback <- ${old#sha256:}"
  else
    echo "  $s: снимок недоступен (первый деплой/перетёртый тег) — откат для него пропущен"
  fi
done

echo "### 2/5 сборка образов (провал здесь = прод не тронут)"
docker compose build

echo "### 3/5 подъём изменённых сервисов (без down, без --build)"
docker compose up -d          # core-api сам выполнит alembic upgrade head на старте

echo "### 4/5 health-check"
if bash .ci/healthcheck.sh; then
  echo "### 5/5 деплой успешен — безопасная чистка диска"
  for s in $SERVICES; do docker rmi "${PREFIX}${s}:rollback" >/dev/null 2>&1 || true; done
  docker image prune -f                              # висячие старые образы (главный пожиратель)
  docker builder prune -f --max-used-space=3GB       # кэш сборки ограничиваем, но не обнуляем
  echo "DEPLOY OK"
  exit 0
fi

echo "!!! health-check провалился — ОТКАТ на прежние образы"
if [ -n "$have_rollback" ]; then
  for s in $SERVICES; do
    if docker image inspect "${PREFIX}${s}:rollback" >/dev/null 2>&1; then
      docker tag "${PREFIX}${s}:rollback" "${PREFIX}${s}:latest"
    fi
  done
  docker compose up -d
  echo "откат выполнен: подняты прежние образы"
else
  echo "снимка для отката нет (первый деплой) — оставляю как есть для диагностики"
fi
echo "DEPLOY FAILED"
exit 1
