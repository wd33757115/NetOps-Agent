# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# 生产环境 - 关闭（Linux）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deployment/docker-compose.yml"
COMPOSE_ARGS=(-f "$COMPOSE_FILE" -f "$PROJECT_ROOT/deployment/docker-compose.ai.yml" --profile core --profile ai)

echo "============================================================"
echo "  NetOps 生产环境 - 关闭"
echo "============================================================"

cd "$PROJECT_ROOT"

if [[ "${1:-}" == "--volumes" ]]; then
  docker compose "${COMPOSE_ARGS[@]}" down -v
else
  docker compose "${COMPOSE_ARGS[@]}" down
fi

echo "[完成] 生产环境已关闭"
