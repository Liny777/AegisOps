#!/usr/bin/env bash
# B9 发布检查（docs/release-checklist.md 的可执行部分）。
# 静态检查：bash scripts/release_check.sh
# 生产门禁：bash scripts/release_check.sh --production-env backend/config/openops.prod.env
set -u
cd "$(dirname "$0")/.."

PRODUCTION_ENV=""
if [ "${1-}" = "--production-env" ]; then
  if [ -z "${2-}" ]; then
    echo "用法：$0 [--production-env <env 文件>]"
    exit 2
  fi
  PRODUCTION_ENV="$2"
  shift 2
fi
if [ "$#" -ne 0 ]; then
  echo "用法：$0 [--production-env <env 文件>]"
  exit 2
fi

FAIL=0

fail() {
  echo "   ✗ $1"
  FAIL=1
}

is_placeholder() {
  case "$1" in
    *REPLACE_WITH*|*CHANGEME*|*'<'*|*'>'*) return 0 ;;
    *) return 1 ;;
  esac
}

require_prod_var() {
  name="$1"
  value="${!name-}"
  if [ -z "$value" ] || is_placeholder "$value"; then
    fail "${name} 未配置或仍是占位值"
  else
    echo "   OK：${name} 已设置"
  fi
}

is_fixed_url() {
  python3 - "$1" "$2" >/dev/null 2>&1 <<'PY'
import sys
import ipaddress
import re
from urllib.parse import urlsplit

value = sys.argv[1]
allowed_schemes = set(sys.argv[2].split(","))

def valid_hostname(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    hostname = hostname.rstrip(".")
    if not hostname or len(hostname) > 253 or not hostname.isascii():
        return False
    label = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    return all(label.fullmatch(part) for part in hostname.split("."))

try:
    parsed = urlsplit(value)
    _port = parsed.port
    valid = (
        parsed.scheme in allowed_schemes
        and bool(parsed.hostname)
        and valid_hostname(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and not any(ch in value for ch in "{}\\")
        and not any(ch.isspace() for ch in value)
    )
except ValueError:
    valid = False
raise SystemExit(0 if valid else 1)
PY
}

is_https_url() {
  is_fixed_url "$1" "https"
}

is_http_url() {
  is_fixed_url "$1" "http,https"
}

is_safe_identifier() {
  value="$1"
  [ "${#value}" -ge 8 ] && [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]]
}


if [ -n "$PRODUCTION_ENV" ]; then
  if [ ! -r "$PRODUCTION_ENV" ]; then
    echo "生产 env 不存在：$PRODUCTION_ENV"
    exit 2
  fi
  # 门禁只审目标文件：先清掉调用者继承的 OPENOPS_*，防止 env 漏项被当前 shell 的旧值掩盖。
  while IFS= read -r inherited_name; do
    unset "$inherited_name"
  done < <(compgen -e | awk '/^OPENOPS_[A-Za-z0-9_]*$/')
  set -a
  # shellcheck disable=SC1090
  if ! source "$PRODUCTION_ENV"; then
    set +a
    echo "生产 env 无法加载：$PRODUCTION_ENV"
    exit 2
  fi
  set +a
fi

echo "== ① DDL 表数核对（core.sql + sql/slices/*.sql 的 CREATE TABLE 计数 vs 基线 35） =="
N=$(cat backend/sql/openops_v1_core.sql backend/sql/slices/*.sql | grep -ci "^create table if not exists")
if [ "$N" -eq 35 ]; then
  echo "   OK：35 表"
else
  fail "表数=$N ≠ 35（新增表时须同步 test_ddl 基线；切片表放 backend/sql/slices/）"
fi

echo "== ② 敏感信息扫描（全部受版本控制文本） =="
PAT='(sk-[A-Za-z0-9]{20,})|(OPENOPS_[A-Z_]*(KEY|TOKEN|COOKIE|PASSWORD)[[:space:]]*=[[:space:]]*["'"'"']?[A-Za-z0-9+/=_-]{20,})|(Cookie:[[:space:]]*[A-Za-z0-9_%-]+=[A-Za-z0-9])|((hwsso_login|login_sid|X-Auth-Token|IAM-Csrf-Token|hwssot3?|hwsso_am|login_uid|suid)[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9_./+=%-]{8,})|(rnd\.huawei\.com)'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  HITS=$(git grep -nEiI "$PAT" -- . \
    ':(exclude)frontend/package-lock.json' ':(exclude)deploy/artifacts/**' 2>/dev/null || true)
  TRACKED_REAL_ENVS=$(git ls-files 'backend/config/openops.*.env')
else
  HITS=$(grep -rInEi "$PAT" backend/src backend/tests backend/config docs scripts deploy 2>/dev/null || true)
  TRACKED_REAL_ENVS=$(find backend/config -maxdepth 1 -type f -name 'openops.*.env' -print 2>/dev/null || true)
fi
if [ -z "$HITS" ]; then
  echo "   OK：无命中"
else
  echo "   命中位置（内容已隐藏）："
  printf '%s\n' "$HITS" | awk -F: '{print "   - " $1 ":" $2}' | sort -u
  FAIL=1
fi
if [ -n "$TRACKED_REAL_ENVS" ]; then
  echo "   发现被跟踪的真实 env："
  echo "$TRACKED_REAL_ENVS"
  FAIL=1
else
  echo "   OK：真实 openops.*.env 未进入版本控制"
fi

echo "== ③ V1 禁用入口核对 =="
if [ -f frontend/src/layout/Sidebar.tsx ]; then
  grep -q "locked: true" frontend/src/layout/Sidebar.tsx \
    && echo "   OK：知识/自动化/本体仍置灰" \
    || fail "Sidebar 置灰项被改动，需人工核对"
else
  echo "   SKIP：backend-only 工件不含前端；该项已在构建机静态检查"
fi

echo "== ④ 测试与类型（此脚本只列出，构建工件前需实际执行） =="
echo "   backend:  python3 -m pytest tests/ -q && .venv/bin/python -m pytest tests/ -q"
echo "   frontend: npx tsc --noEmit && npm run build && npm run e2e"

echo "== ⑤ 生产 env 门禁 =="
if [ -z "$PRODUCTION_ENV" ]; then
  echo "   未传 --production-env：仅完成静态检查；上线前必须带真实 env 再跑一次"
else
  for v in \
    OPENOPS_ENCRYPTION_KEY \
    OPENOPS_PLATFORM_GLM_API_KEY \
    OPENOPS_IAM_ACCESS_TOKEN_URL \
    OPENOPS_IAM_USERINFO_URL \
    OPENOPS_OMODEL_BASE_URL \
    OPENOPS_OMODEL_TENANT_ID \
    OPENOPS_APPTREE_ENTERPRISE_ID \
    OPENOPS_APPTREE_BASE_URL; do
    require_prod_var "$v"
  done

  case "${OPENOPS_IAM_ENABLED-}" in
    true|TRUE|1) echo "   OK：OPENOPS_IAM_ENABLED=true" ;;
    *) fail "OPENOPS_IAM_ENABLED 必须为 true" ;;
  esac
  case "${OPENOPS_OMODEL-}" in
    real) echo "   OK：OPENOPS_OMODEL=real" ;;
    *) fail "OPENOPS_OMODEL 必须为 real" ;;
  esac
  case "${OPENOPS_APPTREE-}" in
    real) echo "   OK：OPENOPS_APPTREE=real" ;;
    *) fail "OPENOPS_APPTREE 必须为 real" ;;
  esac

  # 文根自洽：前端烘焙的 API_BASE 与后端 OPENOPS_ROOT_PATH 必须匹配，否则全部 API 404。
  # 真正剥 /openback 的是后端 RootPathShim，不是网关（给运维的规则 proxy_pass 无尾斜杠
  # ⇒ 不 strip）。所以「dist 打的是 /openback/api」+「ROOT_PATH 留空」= 每个 API 都 404，
  # 而此前所有门禁照样全绿——这条就是补这个洞的。
  BAKED_API_BASE=$(sed -n 's/.*VITE_OPENOPS_API_BASE=\([^[:space:]]*\).*/\1/p' \
    deploy/build-artifacts.sh | head -1)
  if [ -z "$BAKED_API_BASE" ]; then
    fail "无法从 deploy/build-artifacts.sh 解析 VITE_OPENOPS_API_BASE（构建脚本改了？门禁须同步）"
  else
    case "$BAKED_API_BASE" in
      /openback/*)
        if [ "${OPENOPS_ROOT_PATH-}" = "/openback" ]; then
          echo "   OK：dist 烘焙 ${BAKED_API_BASE} 与 OPENOPS_ROOT_PATH=/openback 自洽"
        else
          fail "dist 烘焙 ${BAKED_API_BASE}，但 OPENOPS_ROOT_PATH='${OPENOPS_ROOT_PATH-}' ≠ /openback ⇒ 全部 API 将 404"
        fi
        ;;
      /openops/*)
        # 单文根：后端经前端 nginx 只收裸 /api/*，shim 对裸路径是 no-op ⇒ 留空(阶段4)与
        # /openback(阶段1-3 保留回滚能力)都合法；其它值说明配错了。
        case "${OPENOPS_ROOT_PATH-}" in
          ""|/openback)
            echo "   OK：dist 烘焙 ${BAKED_API_BASE}（单文根）与 OPENOPS_ROOT_PATH='${OPENOPS_ROOT_PATH-}' 自洽" ;;
          *)
            fail "单文根烘焙 ${BAKED_API_BASE} 下 OPENOPS_ROOT_PATH='${OPENOPS_ROOT_PATH-}' 非法（应为 /openback 灰度期，或阶段4留空）" ;;
        esac
        ;;
      *)
        fail "dist 烘焙的 VITE_OPENOPS_API_BASE=${BAKED_API_BASE} 不是已知文根（/openops/api 或 /openback/api）"
        ;;
    esac
  fi

  if [[ ! "${OPENOPS_ENCRYPTION_KEY-}" =~ ^[A-Za-z0-9_-]{43}=$ ]]; then
    fail "OPENOPS_ENCRYPTION_KEY 必须是合法 Fernet key（不输出值）"
  fi
  MODEL_KEY="${OPENOPS_PLATFORM_GLM_API_KEY-}"
  if [ "${#MODEL_KEY}" -lt 16 ]; then
    fail "OPENOPS_PLATFORM_GLM_API_KEY 长度异常"
  fi
  for v in OPENOPS_IAM_ACCESS_TOKEN_URL OPENOPS_IAM_USERINFO_URL; do
    value="${!v-}"
    if is_https_url "$value"; then
      echo "   OK：${v} 是固定 HTTPS 地址"
    else
      fail "${v} 必须是无凭据、查询或片段且含主机的固定 HTTPS 地址"
    fi
  done

  APPTREE_TARGET="${OPENOPS_APPTREE_URL:-${OPENOPS_APPTREE_BASE_URL-}}"
  if is_http_url "$APPTREE_TARGET" && [[ "$APPTREE_TARGET" != *'{host}'* ]] \
     && [[ "$APPTREE_TARGET" != *REPLACE_WITH* ]]; then
    echo "   OK：AppTree 目标为固定 HTTP(S) 地址（未输出具体地址）"
  else
    fail "AppTree URL/BASE_URL 必须是无凭据、查询或片段且含主机的固定 HTTP(S) 地址"
  fi

  OMODEL_BASE="${OPENOPS_OMODEL_BASE_URL-}"
  OMODEL_BASE_VALID=1
  if ! is_https_url "$OMODEL_BASE"; then
    fail "OPENOPS_OMODEL_BASE_URL 必须是无凭据、查询或片段且含主机的固定 HTTPS 地址"
    OMODEL_BASE_VALID=0
  fi
  case "$OMODEL_BASE" in
    *'{host}'*|*REPLACE_WITH*)
      fail "OPENOPS_OMODEL_BASE_URL 禁止请求派生的 {host} 或占位值"
      OMODEL_BASE_VALID=0
      ;;
  esac
  [ "$OMODEL_BASE_VALID" -eq 1 ] && echo "   OK：oModel 目标为固定域（未输出具体地址）"

  if is_safe_identifier "${OPENOPS_OMODEL_TENANT_ID-}" \
     && is_safe_identifier "${OPENOPS_APPTREE_ENTERPRISE_ID-}" \
     && [ "${OPENOPS_OMODEL_TENANT_ID-}" = "${OPENOPS_APPTREE_ENTERPRISE_ID-}" ]; then
    echo "   OK：workspace tenant 与 AppTree 当前企业一致"
  else
    fail "OPENOPS_OMODEL_TENANT_ID 与 OPENOPS_APPTREE_ENTERPRISE_ID 必须一致"
  fi

  for v in OPENOPS_OMODEL_COOKIE OPENOPS_CONSOLE_COOKIE OPENOPS_MCPREGISTRY_COOKIE \
           OPENOPS_APPTREE_COOKIE OPENOPS_SKILLHUB_COOKIE; do
    value="${!v-}"
    if [ -n "$value" ]; then
      fail "${v} 必须未设置（使用当前 IAM 用户登录态透传）"
    else
      echo "   OK：${v} 未设置"
    fi
  done

  case "${OPENOPS_HTTP_DEBUG-}" in
    ""|0|false|FALSE|no|NO) echo "   OK：OPENOPS_HTTP_DEBUG 已关闭" ;;
    *) fail "OPENOPS_HTTP_DEBUG 验收后必须关闭" ;;
  esac
  case "${OPENOPS_TLS_INSECURE-}" in
    ""|0|false|FALSE|no|NO) echo "   OK：OPENOPS_TLS_INSECURE 已关闭" ;;
    *) fail "OPENOPS_TLS_INSECURE 生产必须关闭" ;;
  esac
  case "${OPENOPS_HTTP_TRUST_ENV-}" in
    ""|0|false|FALSE|no|NO) echo "   OK：OPENOPS_HTTP_TRUST_ENV 已关闭" ;;
    *) fail "OPENOPS_HTTP_TRUST_ENV 生产必须关闭，避免环境代理接收 IAM Cookie" ;;
  esac
  for v in OPENOPS_APPTREE_USER_ID OPENOPS_SCOPE_OVERRIDE_APPIDS OPENOPS_BUILD_ID \
           OPENOPS_UNSAFE_LOG_COOKIE; do
    value="${!v-}"
    if [ -n "$value" ]; then
      fail "${v} 是联调/覆盖项，生产必须未设置"
    else
      echo "   OK：${v} 未设置"
    fi
  done
fi

echo "== ⑥ 审计与真链验收（部署后手动） =="
echo "   初始化创建返回 200/workspace_id；oModel 列表可见；owner/tenant/scopes 正确"
echo "   无效 IAM 会话返回 OpenOps 401，且 oModel 日志无对应 POST"
echo "   管理台审计回放可按 trace 串出完整事件链"

if [ "$FAIL" -eq 0 ]; then
  echo "== 结果：已执行的自动项全过 =="
else
  echo "== 结果：存在阻塞项，处理后重跑 =="
fi
exit "$FAIL"
