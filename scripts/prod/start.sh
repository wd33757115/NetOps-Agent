# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# 生产环境 - 一键启动（Compose bootstrap job + 只读检查 + 冒烟）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_BASE="$PROJECT_ROOT/deployment/docker-compose.yml"
COMPOSE_PROD="$PROJECT_ROOT/deployment/docker-compose.prod.yml"

DEV="${DEV:-0}"
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"
WITH_AI="${WITH_AI:-0}"
ENABLE_APPS="${ENABLE_APPS:-}"
WAIT_SECONDS="${WAIT_SECONDS:-60}"

COMPOSE_ARGS=(-f "$COMPOSE_BASE")
if [[ "$DEV" != "1" && -f "$COMPOSE_PROD" ]]; then
    COMPOSE_ARGS+=(-f "$COMPOSE_PROD")
    echo "  Mode: production (127.0.0.1 bind)"
else
    echo "  Mode: dev (all interfaces)"
fi
PROFILE_ARGS=(--profile core)
if [[ "$WITH_AI" == "1" ]]; then
    COMPOSE_ARGS+=(-f "$PROJECT_ROOT/deployment/docker-compose.ai.yml")
    PROFILE_ARGS+=(--profile ai)
fi

echo "============================================================"
echo "  NetOps 生产环境 - 启动"
echo "============================================================"

cd "$PROJECT_ROOT"

ENV_FILE="$PROJECT_ROOT/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
        cp "$PROJECT_ROOT/.env.example" "$ENV_FILE"
        echo "  [WARN] 已从 .env.example 复制 .env，请修改密钥后再上公网"
    else
        echo "  [FAIL] .env 不存在且无 .env.example"
        exit 1
    fi
fi

if [[ "$SKIP_PREFLIGHT" != "1" ]]; then
    echo ""
    echo "==> Environment preflight"
    bash "$SCRIPT_DIR/check_env.sh"
fi

echo ""
echo "==> Docker compose up"
UP_ARGS=(compose "${COMPOSE_ARGS[@]}" --profile core up -d)
if [[ "$WITH_AI" == "1" ]]; then
    UP_ARGS=(compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" up -d)
fi
if [[ "$SKIP_BUILD" != "1" ]]; then
    UP_ARGS+=(--build)
fi
docker "${UP_ARGS[@]}"
echo "  [OK] Containers started"

echo "==> Check locked App dependencies (including DSH SDK/runtime)"
for service in fastapi django; do
    docker compose "${COMPOSE_ARGS[@]}" exec -T -w /app "$service" python -m src.platform.kernel.cli check-dependencies || {
        echo "  [FAIL] App dependencies unavailable in $service; rebuild with SKIP_BUILD=0"
        exit 1
    }
done

echo ""
echo "==> Wait ${WAIT_SECONDS}s for healthchecks"
sleep "$WAIT_SECONDS"

echo ""
echo "==> Check platform database readiness"
docker compose "${COMPOSE_ARGS[@]}" run --rm --no-deps migrate python -m src.platform.database.bootstrap check
APP_PROBE_ARGS=(--gateway-url http://fastapi:8000 --frontend-url http://react --wait-seconds 60)
echo "==> Verify enabled App deployments"
docker compose "${COMPOSE_ARGS[@]}" exec -T django python manage.py app_packages verify "${APP_PROBE_ARGS[@]}"
IFS=',' read -r -a requested_apps <<< "$ENABLE_APPS"
for app_id in "${requested_apps[@]}"; do
    [[ -z "$app_id" ]] && continue
    docker compose "${COMPOSE_ARGS[@]}" exec -T django python manage.py app_packages enable "$app_id" "${APP_PROBE_ARGS[@]}"
done


echo ""
echo "==> Container status"
docker compose "${COMPOSE_ARGS[@]}" "${PROFILE_ARGS[@]}" ps

if [[ "$SKIP_SMOKE" != "1" ]]; then
    echo ""
    echo "==> Smoke test"
    bash "$SCRIPT_DIR/smoke_test.sh" || {
        echo "  [WARN] 冒烟未全部通过，请检查 celery-patrol、celery-control 和 celery-beat 日志"
        exit 1
    }
fi

echo ""
echo "[完成] React: http://127.0.0.1:3000  Django: http://127.0.0.1:8001"
echo "Assistant: http://127.0.0.1:3000/apps/assistant (DSH runs inside Gateway)"
echo "First enablement: ENABLE_APPS=assistant; existing enablement and grants are preserved."
