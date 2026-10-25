# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

#!/usr/bin/env bash
# 生产冒烟测试（BFF 健康 / 鉴权 / 诊断 / Workflow 模板 / viewer RBAC）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deployment/docker-compose.yml"

BFF_BASE="${BFF_BASE:-http://127.0.0.1:8001}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
VIEWER_USER="${VIEWER_USER:-viewer}"
VIEWER_PASSWORD="${VIEWER_PASSWORD:-}"
# No viewer account exists by default; the RBAC check runs when one is given.
SKIP_VIEWER_TEST="${SKIP_VIEWER_TEST:-$([[ -z "$VIEWER_PASSWORD" ]] && echo 1 || echo 0)}"
SKIP_DOCKER_CHECK="${SKIP_DOCKER_CHECK:-0}"

FAILED=0

assert_ok() {
    local name="$1"
    local cond="$2"
    local detail="${3:-}"
    if [[ "$cond" == "1" || "$cond" == "true" || "$cond" == "yes" ]]; then
        echo "  [OK] $name"
    else
        echo "  [FAIL] $name ${detail}"
        FAILED=1
    fi
}

bff_json() {
    local method="${1:-GET}"
    local path="$2"
    shift 2
    curl -sfS -X "$method" \
        -H "Content-Type: application/json" \
        --max-time 60 \
        "$@" \
        "${BFF_BASE}${path}"
}

echo "============================================================"
echo "  NetOps Production - Smoke Test"
echo "  BFF: $BFF_BASE"
echo "============================================================"

if [[ "$SKIP_DOCKER_CHECK" != "1" ]]; then
    if docker compose -f "$COMPOSE_FILE" ps --format json >/dev/null 2>&1; then
        for svc in netops-celery-patrol netops-celery-control netops-celery-beat netops-fastapi netops-django netops-react; do
            state="$(docker compose -f "$COMPOSE_FILE" ps --format '{{.Name}} {{.State}}' 2>/dev/null | awk -v n="$svc" '$1==n {print $2}')"
            if [[ "$state" == "running" ]]; then
                assert_ok "Docker $svc running" 1
            else
                assert_ok "Docker $svc running" 0 "state=${state:-missing}"
            fi
        done
    else
        echo "  [SKIP] Docker compose ps unavailable"
    fi
fi

# 1. Health
if h="$(bff_json GET "/api/health/" 2>/dev/null)"; then
    success="$(echo "$h" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null || echo false)"
    status="$(echo "$h" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('data') or {}).get('status',''))" 2>/dev/null || echo "")"
    if [[ "$success" == "True" || "$success" == "true" ]] && [[ "$status" == "healthy" || "$status" == "degraded" ]]; then
        assert_ok "BFF /api/health/" 1
    else
        assert_ok "BFF /api/health/" 0 "success=$success status=$status"
    fi
else
    assert_ok "BFF /api/health/" 0 "request failed"
fi

# 2. Diagnostics
if d="$(bff_json GET "/api/health/diagnostics/" 2>/dev/null)"; then
    d_ok="$(echo "$d" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo false)"
    assert_ok "BFF /api/health/diagnostics/" "$([[ "$d_ok" == "True" || "$d_ok" == "true" ]] && echo 1 || echo 0)"
    celery_status="$(echo "$d" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in (d.get('data') or {}).get('checks') or []:
    if c.get('id') == 'celery_worker':
        print(c.get('status', ''))
        break
" 2>/dev/null || true)"
    if [[ -n "$celery_status" ]]; then
        assert_ok "Celery Worker (diagnostics)" "$([[ "$celery_status" == "ok" ]] && echo 1 || echo 0)" "$celery_status"
    else
        assert_ok "Celery Worker (diagnostics)" 0 "check missing"
    fi
    celery_beat_status="$(echo "$d" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in (d.get('data') or {}).get('checks') or []:
    if c.get('id') == 'celery_beat':
        print(c.get('status', ''))
        break
" 2>/dev/null || true)"
    if [[ -n "$celery_beat_status" ]]; then
        assert_ok "Celery Beat (diagnostics)" "$([[ "$celery_beat_status" == "ok" ]] && echo 1 || echo 0)" "$celery_beat_status"
    else
        assert_ok "Celery Beat (diagnostics)" 0 "check missing"
    fi
    pg_status="$(echo "$d" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for c in (d.get('data') or {}).get('checks') or []:
    if c.get('id') == 'postgres':
        print(c.get('status', ''))
        break
" 2>/dev/null || true)"
    if [[ -n "$pg_status" ]]; then
        assert_ok "PostgreSQL (diagnostics)" "$([[ "$pg_status" == "ok" ]] && echo 1 || echo 0)" "$pg_status"
    fi
else
    assert_ok "BFF /api/health/diagnostics/" 0 "request failed"
fi

# 3. Admin login
TOKEN=""
if login="$(bff_json POST "/api/auth/login/" -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASSWORD\"}" 2>/dev/null)"; then
    TOKEN="$(echo "$login" | python3 -c "import sys,json; print((json.load(sys.stdin).get('data') or {}).get('access',''))" 2>/dev/null || true)"
    assert_ok "Admin login" "$([[ -n "$TOKEN" ]] && echo 1 || echo 0)"
else
    assert_ok "Admin login" 0 "request failed"
fi

if [[ -n "$TOKEN" ]]; then
    if me="$(bff_json GET "/api/auth/me/" -H "Authorization: Bearer $TOKEN" 2>/dev/null)"; then
        me_user="$(echo "$me" | python3 -c "import sys,json; print((json.load(sys.stdin).get('data') or {}).get('username',''))" 2>/dev/null || true)"
        assert_ok "GET /api/auth/me/" "$([[ "$me_user" == "$ADMIN_USER" ]] && echo 1 || echo 0)"
    else
        assert_ok "GET /api/auth/me/" 0 "request failed"
    fi

    if tpl="$(bff_json GET "/api/workflows/templates/" -H "Authorization: Bearer $TOKEN" 2>/dev/null)"; then
        tpl_ok="$(echo "$tpl" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo false)"
        assert_ok "Workflow templates API" "$([[ "$tpl_ok" == "True" || "$tpl_ok" == "true" ]] && echo 1 || echo 0)"
        has_itsm="$(echo "$tpl" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [x.get('name') for x in (d.get('data') or []) if isinstance(x, dict)]
print('itsm-firewall-change' in names)
" 2>/dev/null || echo False)"
        assert_ok "Template itsm-firewall-change" "$([[ "$has_itsm" == "True" || "$has_itsm" == "true" ]] && echo 1 || echo 0)"
    else
        assert_ok "Workflow templates API" 0 "request failed"
    fi

    if skills="$(bff_json GET "/api/skills/" -H "Authorization: Bearer $TOKEN" 2>/dev/null)"; then
        skills_ok="$(echo "$skills" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('success') or d.get('data') is not None)
" 2>/dev/null || echo false)"
        assert_ok "Skills list API" "$([[ "$skills_ok" == "True" || "$skills_ok" == "true" ]] && echo 1 || echo 0)"
    else
        assert_ok "Skills list API" 0 "request failed"
    fi

    if apps="$(bff_json GET "/api/platform/apps/" -H "Authorization: Bearer $TOKEN" 2>/dev/null)"; then
        apps_ok="$(echo "$apps" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success') is True)" 2>/dev/null || echo false)"
        assert_ok "App catalog API (disabled Apps may be absent)" "$([[ "$apps_ok" == "True" ]] && echo 1 || echo 0)"
    else
        assert_ok "App catalog API" 0 "request failed"
    fi
fi

# 4. Viewer RBAC
if [[ "$SKIP_VIEWER_TEST" != "1" ]]; then
    if vlogin="$(bff_json POST "/api/auth/login/" -d "{\"username\":\"$VIEWER_USER\",\"password\":\"$VIEWER_PASSWORD\"}" 2>/dev/null)"; then
        VTOKEN="$(echo "$vlogin" | python3 -c "import sys,json; print((json.load(sys.stdin).get('data') or {}).get('access',''))" 2>/dev/null || true)"
        assert_ok "Viewer login" "$([[ -n "$VTOKEN" ]] && echo 1 || echo 0)"
        if [[ -n "$VTOKEN" ]]; then
            code="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
                -H "Authorization: Bearer $VTOKEN" \
                -H "Content-Type: application/json" \
                --max-time 60 \
                "${BFF_BASE}/api/skills/reload-all/" || echo 000)"
            assert_ok "Viewer denied skill reload (403)" "$([[ "$code" == "403" ]] && echo 1 || echo 0)" "status=$code"
        fi
    else
        assert_ok "Viewer login" 0 "request failed"
    fi
fi

# 5. React 静态
WEB_BASE="${BFF_BASE/:8001/:3000}"
[[ "$WEB_BASE" == "$BFF_BASE" ]] && WEB_BASE="http://127.0.0.1:3000"
if html="$(curl -sfS --max-time 15 "$WEB_BASE/" 2>/dev/null)"; then
    if echo "$html" | grep -q root; then
        assert_ok "React index" 1
    else
        assert_ok "React index" 0 "missing root element"
    fi
else
    assert_ok "React index" 0 "request failed"
fi

echo ""
if [[ "$FAILED" -ne 0 ]]; then
    echo "Smoke test FAILED"
    exit 1
fi
echo "Smoke test PASSED"
exit 0
