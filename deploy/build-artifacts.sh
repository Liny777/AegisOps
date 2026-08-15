#!/usr/bin/env bash
# Mac 侧一键打包内网部署工件（deploy/artifacts/）：
#   ① openops-frontend-image.tar.gz  前端一体镜像 nginx+dist（linux/amd64；测试/生产共用，
#                                    后端机地址经 env 注入 nginx 模板，不烘焙进镜像）
#   ② openops-sidecar-image.tar.gz   sidecar 镜像（linux/amd64）
#   镜像均 gzip（docker load 原生认 .tar.gz）——内网单文件上传限 500MB，sidecar 裸 tar 998M 超限
#   ③ openops-backend-src-<BUILD_ID>.tar.gz  后端代码包（内网每 release 独立 venv）
# 用法：
#   bash deploy/build-artifacts.sh [版本标签，默认 git short sha]
#   bash deploy/build-artifacts.sh --backend-only [版本标签]  # 仅生成版本化完整后端包，不碰旧工件
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="full"
if [[ "${1:-}" == "--backend-only" ]]; then
  MODE="backend-only"
  shift
fi
if [[ "$#" -gt 1 ]]; then
  echo "用法：$0 [--backend-only] [版本标签]"
  exit 2
fi

LABEL="${1:-}"
if [[ -n "$LABEL" && ! "$LABEL" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]{0,46}[A-Za-z0-9])?$ ]]; then
  echo "版本标签须以字母/数字开头结尾、长度 1-48，中间只允许字母/数字/点/下划线/连字符"
  exit 2
fi
HEAD_SHA="$(git rev-parse HEAD)"
HEAD_SHORT="$(git rev-parse --short HEAD)"
DIRTY="false"
WORKTREE_SHA="$(git diff --binary HEAD -- | shasum -a 256 | awk '{print $1}')"
VER="${LABEL:+${LABEL}-}${HEAD_SHORT}"
if ! git diff --quiet HEAD --; then
  DIRTY="true"
  VER="${VER}-wt${WORKTREE_SHA:0:8}"
fi
OUT_ROOT="$PWD/deploy/artifacts"
OUT="$OUT_ROOT/$VER"
BUILD_TMP=""
STAGING_OUT=""
OUTPUT_LOCK=""
BACKEND_NAME="openops-backend-src-${VER}.tar.gz"

ARCHIVE_PATHS=(
  backend/src backend/sql backend/tests backend/docs
  backend/run.py backend/pyproject.toml backend/run-backend.sh backend/config
  # 诊断工具箱：deploy-intranet.md §五「故障排查」与 EXTERNAL-INTEGRATION.md 都让人在
  # 后端机上跑它们，此前却没进包（只能手工 scp）。check-net ③ 是判定 omodel 403 归属的
  # 决定性工具——它必须跟着 release 走，否则出问题时机上根本没有这个文件。
  backend/check-net.py backend/check-db.py
  scripts/release_check.sh
  docs/API.md docs/release-checklist.md docs/deploy-intranet.md docs/build-images-intranet.md
  deploy/systemd/openops-backend.service
)
UNTRACKED=$(git ls-files --others --exclude-standard -- "${ARCHIVE_PATHS[@]}")
if [[ -n "$UNTRACKED" ]]; then
  echo "后端发布范围存在未跟踪文件；请先纳入版本控制或移出发布范围："
  printf '%s\n' "$UNTRACKED"
  exit 1
fi

cleanup() {
  if [[ -n "$BUILD_TMP" && -d "$BUILD_TMP" ]]; then
    rm -rf "$BUILD_TMP"
  fi
  if [[ -n "$STAGING_OUT" && -d "$STAGING_OUT" ]]; then
    rm -rf "$STAGING_OUT"
  fi
  if [[ -n "$OUTPUT_LOCK" && -d "$OUTPUT_LOCK" ]]; then
    rmdir "$OUTPUT_LOCK" 2>/dev/null || true
  fi
}
trap cleanup EXIT

start_output() {
  local lock_candidate
  if [[ -e "$OUT" ]]; then
    echo "拒绝复用不可变版本目录：$OUT"
    return 1
  fi
  mkdir -p "$OUT_ROOT"
  lock_candidate="$OUT_ROOT/.${VER}.lock"
  if ! mkdir "$lock_candidate"; then
    echo "同一 BUILD_ID 已有构建在运行或遗留锁：$lock_candidate"
    return 1
  fi
  # 只登记本进程成功创建的锁；失败竞争者的 EXIT trap 绝不能删除持有者的锁。
  OUTPUT_LOCK="$lock_candidate"
  STAGING_OUT="$(mktemp -d "$OUT_ROOT/.${VER}.tmp.XXXXXX")"
}

publish_output() {
  if [[ -e "$OUT" ]]; then
    echo "发布工件时发现版本目录已存在，拒绝覆盖：$OUT"
    return 1
  fi
  mv "$STAGING_OUT" "$OUT"
  STAGING_OUT=""
  rmdir "$OUTPUT_LOCK"
  OUTPUT_LOCK=""
}

build_backend_archive() {
  local destination="$1"
  local built_at
  local current_head current_worktree current_untracked
  if [[ -e "$destination" || -e "${destination}.sha256" ]]; then
    echo "拒绝覆盖不可变工件：$destination"
    return 1
  fi
  current_head="$(git rev-parse HEAD)"
  current_worktree="$(git diff --binary HEAD -- | shasum -a 256 | awk '{print $1}')"
  current_untracked="$(git ls-files --others --exclude-standard -- "${ARCHIVE_PATHS[@]}")"
  if [[ "$current_head" != "$HEAD_SHA" || "$current_worktree" != "$WORKTREE_SHA" \
        || -n "$current_untracked" ]]; then
    echo "测试期间源码发生变化，拒绝生成版本标识不一致的工件；请重新执行打包"
    return 1
  fi
  built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  BUILD_TMP="$(mktemp -d)"

  # 只复制 Git 跟踪文件：真实 env、.venv、egg-info、缓存和历史工件不可能混入发布包。
  git ls-files -z -- "${ARCHIVE_PATHS[@]}" \
    | tar --null -cf - -T - | tar -xf - -C "$BUILD_TMP"

  printf '%s\n' \
    "BUILD_ID=$VER" \
    "GIT_COMMIT=$HEAD_SHA" \
    "WORKTREE_SHA256=$WORKTREE_SHA" \
    "DIRTY=$DIRTY" \
    "BUILT_AT_UTC=$built_at" \
    > "$BUILD_TMP/BUILD_INFO"
  tar -czf "$destination" -C "$BUILD_TMP" .
  rm -rf "$BUILD_TMP"
  BUILD_TMP=""
}

run_backend_tests() {
  local system_python
  echo "== 后端发布回归（两套解释器，串行避免共享测试库互扰） =="
  system_python="${OPENOPS_TEST_PYTHON:-$(command -v python3 || true)}"
  if [[ -z "$system_python" || "$system_python" == "$PWD/backend/.venv/"* ]]; then
    echo "未找到独立于 backend/.venv 的 python3；可用 OPENOPS_TEST_PYTHON 显式指定"
    return 1
  fi
  (cd backend && "$system_python" -m pytest -q)
  if [[ ! -x backend/.venv/bin/python ]]; then
    echo "缺 backend/.venv/bin/python，无法执行 AgentScope 依赖回归"
    return 1
  fi
  (cd backend && .venv/bin/python -m pytest -q)
}

bash scripts/release_check.sh

if [[ "$MODE" == "backend-only" ]]; then
  start_output
  run_backend_tests
  build_backend_archive "$STAGING_OUT/$BACKEND_NAME"
  (cd "$STAGING_OUT" && shasum -a 256 "$BACKEND_NAME" > "${BACKEND_NAME}.sha256")
  publish_output
  echo "== 完成：$OUT/$BACKEND_NAME =="
  cat "$OUT/${BACKEND_NAME}.sha256"
  exit 0
fi

start_output

echo "== ① 前端一体镜像（nginx+dist；构建强制 real——.env.local 的 mock 会被 vite build 读到，进程 env 压制）=="
# 单文根（域名部署 xxxx.com/openops 一条规则；API 走前端 nginx 内部转发到本机 uvicorn）。
# BASE 决定静态/Router basename/CopilotKit runtimeUrl；API_BASE 决定 REST/SSE/agui 目标。
# 两者都是 vite 构建期常量折叠进 bundle，env 事后补救不了——换文根须重打前端镜像。
( cd frontend && VITE_OPENOPS_API_MODE=real VITE_OPENOPS_TRANSPORT=agui \
  VITE_OPENOPS_BASE=/openops/ VITE_OPENOPS_API_BASE=/openops/api npm run build )
# 防 mock 烘焙断言：API_MODE=real 时 mock facade 会被常量折叠+树摇整体移除——
# dist 里出现 mock 指纹字符串 = 烘焙错了（.env.local 的 mock 污染）
if grep -rq "mock 演示" frontend/dist/assets 2>/dev/null; then
  echo "   ✗ dist 含 mock 指纹——API_MODE 烘焙成 mock！检查构建 env"; exit 1
fi
echo "   API_MODE 烘焙=real ✓（mock facade 已被树摇移除）"
# 文根烘焙断言（与 docs/build-images-intranet.md 同源）。BASE 落 index.html 的资源路径；
# API_BASE 落 assets/*.js（client.ts 的常量），index.html 看不见它，故两处分别断言。
grep -q '/openops/assets' frontend/dist/index.html \
  || { echo "   ✗ 前端文根未烘焙进 dist（VITE_OPENOPS_BASE）"; exit 1; }
if grep -q "Program Files" frontend/dist/index.html; then
  echo "   ✗ 文根被 Git Bash 路径翻译污染（见 docs/build-images-intranet.md §六）"; exit 1
fi
# API_BASE 未注入时会静默回落到 client.ts 的默认 "/api"——那是生产唯一不可路由的值
# （网关只认 /openops），且上面几条断言全绿。故必须正向断言 + 反向查旧文根残留。
# ⚠断言必须带引号锚定：CopilotChatPanel 的 runtimeUrl=`${BASE_URL}api/copilotkit` 会被
# vite 常量折叠成 "/openops/api/copilotkit"，所以裸 grep '/openops/api' 在 API_BASE
# 完全没注入时**照样命中**（实测），是个永不报警的假门禁。带引号只匹配 API_BASE 那个独立字面量。
grep -rEq "[\"']/openops/api[\"']" frontend/dist/assets \
  || { echo "   ✗ API_BASE 未烘焙成 /openops/api（回落默认 /api 会全量 404）"; exit 1; }
if grep -rq '/openback' frontend/dist/assets; then
  echo "   ✗ dist 仍含已退役的 /openback 文根——检查构建 env"; exit 1
fi
echo "   文根烘焙=/openops/ + API_BASE=/openops/api ✓"
# dist staging 进构建上下文（deploy/frontend/dist 已 gitignore），打完即清
rm -rf deploy/frontend/dist && cp -R frontend/dist deploy/frontend/dist
docker build --platform linux/amd64 -f deploy/frontend/Dockerfile \
  -t "openops-frontend:${VER}" -t openops-frontend:latest deploy/frontend
rm -rf deploy/frontend/dist
docker save "openops-frontend:${VER}" openops-frontend:latest | gzip > "$STAGING_OUT/openops-frontend-image.tar.gz"
echo "   → $OUT/openops-frontend-image.tar.gz"

echo "== ② sidecar 镜像（linux/amd64）=="
# 用 docker 引擎默认 builder（容器驱动的自建 buildx builder 可能无网导致 DeadlineExceeded）
docker build --platform linux/amd64 -f deploy/sidecar/Dockerfile \
  -t "openops-sidecar:${VER}" -t openops-sidecar:latest frontend/
docker save "openops-sidecar:${VER}" openops-sidecar:latest | gzip > "$STAGING_OUT/openops-sidecar-image.tar.gz"
echo "   → $OUT/openops-sidecar-image.tar.gz"

echo "== ③ 后端代码包 =="
run_backend_tests
build_backend_archive "$STAGING_OUT/$BACKEND_NAME"
(cd "$STAGING_OUT" && shasum -a 256 "$BACKEND_NAME" > "${BACKEND_NAME}.sha256")
echo "   → $OUT/$BACKEND_NAME"

echo "== ④ 部署配套（compose/env 模板/systemd 打一个小包）=="
tar -czf "$STAGING_OUT/openops-deploy-conf.tar.gz" deploy/systemd \
  deploy/frontend/docker-compose.yml \
  deploy/frontend/frontend.test.env.example deploy/frontend/frontend.prod.env.example \
  deploy/sidecar/sidecar.test.env.example deploy/sidecar/sidecar.prod.env.example
echo "   → $OUT/openops-deploy-conf.tar.gz"

( cd "$STAGING_OUT" && shasum -a 256 \
    openops-frontend-image.tar.gz openops-sidecar-image.tar.gz \
    "$BACKEND_NAME" openops-deploy-conf.tar.gz > SHA256SUMS && cat SHA256SUMS )
publish_output
echo "== 完成：版本 ${VER}，工件在 $OUT/ =="
