# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# 生产环境变量预检：默认密钥 / 必填项
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"

FAILED=0
WARNED=0

fail() { echo "  [FAIL] $1"; FAILED=1; }
warn() { echo "  [WARN] $1"; WARNED=1; }
ok()   { echo "  [OK]   $1"; }

echo "============================================================"
echo "  NetOps Production - Environment Preflight"
echo "============================================================"

if [[ ! -f "$ENV_FILE" ]]; then
    fail ".env 不存在：$ENV_FILE （请 cp .env.example .env）"
    exit 1
fi

get_env() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^["'\'']//;s/["'\'']$//' || true
}

is_weak() {
    local name="$1"
    local value="$2"
    [[ -z "$value" ]] && return 0
    local patterns=(change-me netops123456 minioadmin django-insecure itsm-secret-2026 my-secret-key guest admin123)
    for p in "${patterns[@]}"; do
        if [[ "$value" == "$p" ]] || [[ "$value" == *"$p"* ]]; then
            warn "$name 仍含默认/弱口令片段"
            return 0
        fi
    done
    return 1
}

if [[ -z "$(get_env DEEPSEEK_API_KEY)" && -z "$(get_env LLM_API_KEY)" ]]; then
    warn "No LLM_API_KEY/DEEPSEEK_API_KEY; model-backed App operations will be unavailable"
else
    ok "DEEPSEEK_API_KEY 已设置"
fi
app_secret="$(get_env NETOPS_APP_INTERNAL_SECRET)"
if [[ "${#app_secret}" -lt 32 ]]; then
    fail "NETOPS_APP_INTERNAL_SECRET must contain at least 32 characters for App authorization"
fi

if [[ -z "$(get_env DEVICE_ACCESS_CREDENTIAL_KEY)" ]]; then
    warn "DEVICE_ACCESS_CREDENTIAL_KEY 未设置；SSH 会话密码和临时设备凭据无法加密保存。生成：python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
fi

for key in SECRET_KEY JWT_SECRET_KEY POSTGRES_PASSWORD MINIO_ACCESS_KEY MINIO_SECRET_KEY ITSM_WEBHOOK_SECRET; do
    val="$(get_env "$key")"
    if [[ -z "$val" ]]; then
        warn "$key 未设置"
    else
        is_weak "$key" "$val" || true
    fi
done

dj_debug="$(get_env DJANGO_DEBUG)"
if [[ "$dj_debug" == "true" || "$dj_debug" == "True" ]]; then
    warn "DJANGO_DEBUG=true（生产应为 false）"
else
    ok "DJANGO_DEBUG 非 true"
fi

debug="$(get_env DEBUG)"
if [[ "$debug" == "true" || "$debug" == "True" ]]; then
    warn "DEBUG=true（生产应为 false）"
fi

enforce="$(get_env ENFORCE_BFF_ORIGIN)"
if [[ "$enforce" == "false" ]]; then
    warn "ENFORCE_BFF_ORIGIN=false（生产建议 true）"
else
    ok "ENFORCE_BFF_ORIGIN 已启用或未显式关闭"
fi

log_fmt="$(get_env LOG_FORMAT)"
if [[ "$log_fmt" != "json" ]]; then
    warn "LOG_FORMAT 非 json（生产建议 json 便于采集）"
else
    ok "LOG_FORMAT=json"
fi

if [[ -z "$(get_env CELERY_BROKER_URL)" ]]; then
    warn "CELERY_BROKER_URL 未设置"
fi

echo ""
if [[ "$FAILED" -ne 0 ]]; then
    echo "预检未通过，请修正 .env 后重试。"
    exit 1
fi
if [[ "$WARNED" -ne 0 ]]; then
    echo "预检完成（有警告，上线前建议处理）。"
    exit 0
fi
echo "预检通过。"
exit 0
