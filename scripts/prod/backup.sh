# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# PostgreSQL / MinIO / Django SQLite 备份
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_BASE="$PROJECT_ROOT/deployment/docker-compose.yml"
COMPOSE_PROD="$PROJECT_ROOT/deployment/docker-compose.prod.yml"

OUT_DIR="${1:-}"
SKIP_MINIO="${SKIP_MINIO:-0}"
SKIP_SQLITE="${SKIP_SQLITE:-0}"

COMPOSE_ARGS=(-f "$COMPOSE_BASE")
if [[ -f "$COMPOSE_PROD" ]]; then
    COMPOSE_ARGS+=(-f "$COMPOSE_PROD")
fi

TS="$(date +%Y%m%d-%H%M%S)"
if [[ -z "$OUT_DIR" ]]; then
    OUT_DIR="$PROJECT_ROOT/backups/$TS"
fi
mkdir -p "$OUT_DIR"

echo "============================================================"
echo "  NetOps Production - Backup"
echo "  Output: $OUT_DIR"
echo "============================================================"

ENV_FILE="$PROJECT_ROOT/.env"
MINIO_USER="minioadmin"
MINIO_PASS="minioadmin"
MINIO_BUCKET="netops-files"
if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key val; do
        [[ "$key" =~ ^# ]] && continue
        val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
        case "$key" in
            MINIO_ACCESS_KEY) MINIO_USER="$val" ;;
            MINIO_SECRET_KEY) MINIO_PASS="$val" ;;
            MINIO_BUCKET_NAME) MINIO_BUCKET="$val" ;;
        esac
    done < <(grep -E '^(MINIO_ACCESS_KEY|MINIO_SECRET_KEY|MINIO_BUCKET_NAME)=' "$ENV_FILE" || true)
fi

echo ""
echo "==> PostgreSQL dump"
PG_FILE="$OUT_DIR/postgres-netops_agent.sql"
docker compose "${COMPOSE_ARGS[@]}" exec -T postgres pg_dump -U netops -d netops_agent > "$PG_FILE"
echo "  [OK] postgres -> $PG_FILE"

if [[ "$SKIP_MINIO" != "1" ]]; then
    echo ""
    echo "==> MinIO bucket mirror ($MINIO_BUCKET)"
    MINIO_OUT="$OUT_DIR/minio-$MINIO_BUCKET"
    mkdir -p "$MINIO_OUT"
    NETWORK="$(docker inspect netops-minio --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null || true)"
    if [[ -z "$NETWORK" ]]; then
        echo "  [WARN] netops-minio 未运行，跳过 MinIO 备份"
    else
        docker run --rm \
            --network "$NETWORK" \
            -v "${MINIO_OUT}:/backup" \
            pgsty/mc@sha256:6ecdaec70bc5e8cf9d0e9a70c190f3beb1c83f11617ff4333f1fcd71ab1daf18 \
            /bin/sh -c "mc alias set local http://minio:9000 '${MINIO_USER}' '${MINIO_PASS}' && mc mirror --overwrite local/${MINIO_BUCKET} /backup/"
        echo "  [OK] minio -> $MINIO_OUT"
    fi
fi

if [[ "$SKIP_SQLITE" != "1" ]]; then
    echo ""
    echo "==> Django SQLite (auth users)"
    SQLITE_SRC="$PROJECT_ROOT/web/django_backend/db.sqlite3"
    if [[ -f "$SQLITE_SRC" ]]; then
        cp "$SQLITE_SRC" "$OUT_DIR/django-db.sqlite3"
        echo "  [OK] sqlite copied"
    else
        echo "  [WARN] db.sqlite3 不存在，跳过"
    fi
fi

cat > "$OUT_DIR/manifest.json" <<EOF
{
  "timestamp": "$TS",
  "postgres": "postgres-netops_agent.sql",
  "minio": $([[ "$SKIP_MINIO" == "1" ]] && echo "null" || echo "\"minio-$MINIO_BUCKET\""),
  "django_sqlite": $([[ "$SKIP_SQLITE" == "1" ]] && echo "null" || echo "\"django-db.sqlite3\"")
}
EOF

echo ""
echo "[完成] 备份目录: $OUT_DIR"
