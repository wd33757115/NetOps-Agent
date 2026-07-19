# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# 生产环境 - 一键启动（Compose + 迁移 + 种子用户 + 冒烟）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_BASE="$PROJECT_ROOT/deployment/docker-compose.yml"
COMPOSE_PROD="$PROJECT_ROOT/deployment/docker-compose.prod.yml"

DEV="${DEV:-0}"
USE_V2="${USE_SUPERVISOR_V2:-true}"
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-0}"
SKIP_SEED="${SKIP_SEED:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"
WAIT_SECONDS="${WAIT_SECONDS:-60}"

export USE_SUPERVISOR_V2="$USE_V2"

COMPOSE_ARGS=(-f "$COMPOSE_BASE")
if [[ "$DEV" != "1" && -f "$COMPOSE_PROD" ]]; then
    COMPOSE_ARGS+=(-f "$COMPOSE_PROD")
    echo "  Mode: production (127.0.0.1 bind)"
else
    echo "  Mode: dev (all interfaces)"
fi

echo "============================================================"
echo "  NetOps 生产环境 - 启动 (USE_SUPERVISOR_V2=$USE_V2)"
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
if [[ "$SKIP_BUILD" != "1" ]]; then
    UP_ARGS+=(--build)
fi
docker "${UP_ARGS[@]}"
echo "  [OK] Containers started"

echo ""
echo "==> Wait ${WAIT_SECONDS}s for healthchecks"
sleep "$WAIT_SECONDS"

echo ""
echo "==> Django migrate"
docker compose "${COMPOSE_ARGS[@]}" exec -T django python manage.py migrate --noinput || \
    echo "  [WARN] migrate 可能需容器就绪后重试"

if [[ "$SKIP_SEED" != "1" ]]; then
    echo ""
    echo "==> Seed auth users"
    docker compose "${COMPOSE_ARGS[@]}" exec -T django python manage.py seed_auth_users || \
        echo "  [WARN] seed_auth_users skipped or failed"
fi

echo ""
echo "==> Container status"
docker compose "${COMPOSE_ARGS[@]}" --profile core ps

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
