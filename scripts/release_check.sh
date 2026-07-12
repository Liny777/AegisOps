#!/usr/bin/env bash
# B9 发布检查（docs/release-checklist.md 的可执行部分）。在 repo 根目录跑：bash scripts/release_check.sh
set -u
cd "$(dirname "$0")/.."
FAIL=0

echo "== ① DDL 表数核对（core.sql CREATE TABLE 计数 vs 基线 26） =="
N=$(grep -ci "^create table if not exists" backend/sql/openops_v1_core.sql)
if [ "$N" -eq 26 ]; then echo "   OK：26 表"; else echo "   ⚠ 表数=$N ≠ 26（新增表？同步 conftest TABLES 与 test_ddl 基线）"; FAIL=1; fi

echo "== ② 敏感信息扫描（源码不得出现真实凭证/内网主机指纹） =="
PAT='(sk-[A-Za-z0-9]{8,})|(OPENOPS_[A-Z_]*KEY[[:space:]]*=[[:space:]]*["'"'"'][^"'"'"']{8,})|(rnd\.huawei\.com)|(Cookie:[[:space:]]*[A-Za-z0-9_]+=[A-Za-z0-9])'
HITS=$(grep -rInE "$PAT" backend/src frontend/src frontend/server 2>/dev/null | grep -v "示例\|example\|<你的\|占位")
if [ -z "$HITS" ]; then echo "   OK：无命中"; else echo "   ⚠ 命中："; echo "$HITS"; FAIL=1; fi

echo "== ③ V1 禁用入口核对（不应出现已放开的入口） =="
grep -q "locked: true" frontend/src/layout/Sidebar.tsx && echo "   OK：知识/自动化/本体仍置灰" || { echo "   ⚠ Sidebar 置灰项被改动，人工核"; FAIL=1; }

echo "== ④ 测试与类型（双解释器 pytest + tsc；此处只提示命令不代跑） =="
echo "   backend:  python3 -m pytest tests/ -q && .venv/bin/python -m pytest tests/ -q"
echo "   frontend: npx tsc --noEmit && npm run build && npm run e2e"

echo "== ⑤ 生产 env 必配核对（缺一不可上线） =="
for v in OPENOPS_ENCRYPTION_KEY OPENOPS_PLATFORM_GLM_API_KEY OPENOPS_IAM_ENABLED; do
  echo "   - $v（当前 shell：${!v:+已设}${!v:-未设——部署环境须确认}）"
done
echo "   - OPENOPS_IAM_ENABLED=true 时须齐：OPENOPS_IAM_ACCESS_TOKEN_URL / OPENOPS_IAM_USERINFO_URL（可选 LOGIN_URL/SIGNOUT_URL/LOGIN_KEY_FIELD/DISPLAY_NAME_FIELD）"
echo "   - 治理 env 见 backend/docs/EXTERNAL-INTEGRATION.md「运行时治理旋钮」"

echo "== ⑥ 审计串联抽查（起后端后手动） =="
echo "   管理台→审计回放：点任一事件 trace 徽标 → 应串出该 run 全链事件；白名单增删应有 whitelist.granted/revoked"

[ "$FAIL" -eq 0 ] && echo "== 结果：自动项全过（手动项按上述执行） ==" || echo "== 结果：有 ⚠ 项，处理后重跑 =="
exit $FAIL
