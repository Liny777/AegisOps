#!/usr/bin/env bash
# 内网前端一体镜像打包（Windows Git Bash + WSL docker）。单文根 /openops 收敛版。
# 与 deploy/build-artifacts.sh（Mac 外网侧一键打包）同源，只是拆成内网可逐条跑的版本。
# 用法（在 Git Bash 里）：bash deploy/frontend/build-frontend-image.sh
#
# 关键点：
#   - 文根两 env（BASE / API_BASE）直接写在构建命令行，脚本自成单一事实源，不再依赖
#     frontend/.env.production.local（那个文件被 gitignore，很容易残留旧的 /openback/api，
#     且现有断言看不见它 → 静默全量 404）。
#   - Windows Git Bash 的 MSYS 会把 /openops/ 这类路径型 env 值改写成 Windows 路径
#     （C:/Program Files/Git/...）。下面关掉参数与 env 两种翻译；即便漏了，第 2 步的
#     "Program Files" 与 API_BASE 断言也会在打镜像前拦住，绝不产出错镜像。

# 仓库根：从脚本自身位置推导（脚本在 deploy/frontend/ 下），比写死路径稳。
# 若你的构建机习惯固定路径，也可改回： cd /f/Python/AegisOps-main/AegisOps-main
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)" || exit 1
cd "$REPO_ROOT" || exit 1

# Git 提交短版本号；不在 Git 仓库时用当前时间
VER=$(git rev-parse --short HEAD 2>/dev/null)
if [ -z "$VER" ]; then
  VER=$(date +%m%d%H%M)
fi
echo "构建版本: ${VER}"

# 1. 构建 dist —— 单文根烘焙（BASE=/openops/、API_BASE=/openops/api）。
#    MSYS_NO_PATHCONV=1 关参数翻译；MSYS2_ENV_CONV_EXCL='*' 关 env 值翻译（双保险）。
(
  cd frontend || exit 1
  export MSYS_NO_PATHCONV=1
  export MSYS2_ENV_CONV_EXCL='*'

  VITE_OPENOPS_API_MODE=real \
  VITE_OPENOPS_TRANSPORT=agui \
  VITE_OPENOPS_BASE=/openops/ \
  VITE_OPENOPS_API_BASE=/openops/api \
  npm run build
) || {
  echo "✗ 前端构建失败"
  exit 1
}

# 2. 构建产物断言（放在 docker build 之前 = fail fast；任一命中即作废重建）
if grep -rq "mock 演示" frontend/dist/assets; then
  echo "✗ dist 含 mock 指纹，镜像构建终止"
  exit 1
fi
grep -q '/openops/assets' frontend/dist/index.html \
  || { echo "✗ 前端文根未烘焙进 dist（VITE_OPENOPS_BASE）"; exit 1; }
if grep -q "Program Files" frontend/dist/index.html; then
  echo "✗ 文根被 Git Bash 路径翻译污染（见 docs/build-images-intranet.md §六）"
  exit 1
fi
# API_BASE 落 assets/*.js（client.ts 的常量），index.html 看不见它，故单独断言。
# ⚠必须带引号锚定：CopilotChatPanel 的 runtimeUrl=`${BASE_URL}api/copilotkit` 会被 vite
#   折叠成 "/openops/api/copilotkit"，裸 grep '/openops/api' 在 API_BASE 完全没注入时照样
#   命中（实测），是永不报警的假门禁。带引号只匹配 API_BASE 那个独立字面量。
grep -rEq "[\"']/openops/api[\"']" frontend/dist/assets \
  || { echo "✗ API_BASE 未烘焙成 /openops/api（回落默认 /api 会全量 404）"; exit 1; }
if grep -rq '/openback' frontend/dist/assets; then
  echo "✗ dist 仍含已退役的 /openback 文根"; exit 1
fi
echo "✓ 前端 dist 构建成功（文根 /openops/ + API_BASE /openops/api）"

# 3. 将 dist 放入 Docker 构建上下文
rm -rf deploy/frontend/dist
cp -R frontend/dist deploy/frontend/dist

# 4. Windows Git Bash 路径 → WSL 路径，用 WSL 里的 docker 构建
REPO_WIN=$(pwd -W)
REPO_WSL=$(MSYS_NO_PATHCONV=1 wsl.exe wslpath -u "$REPO_WIN" | tr -d '\r')
echo "Windows 仓库路径: ${REPO_WIN}"
echo "WSL 仓库路径: ${REPO_WSL}"

MSYS_NO_PATHCONV=1 wsl.exe bash -lc "
  cd '$REPO_WSL' &&
  docker build \
    -f deploy/frontend/Dockerfile \
    -t 'openops-frontend:${VER}' \
    -t 'openops-frontend:latest' \
    deploy/frontend
"
BUILD_RESULT=$?

# 5. 无论成败都清理临时 staging 目录
rm -rf deploy/frontend/dist

if [ "$BUILD_RESULT" -ne 0 ]; then
  echo "✗ Docker 镜像构建失败"
  exit "$BUILD_RESULT"
fi

echo "✓ Docker 镜像构建完成"
echo "  openops-frontend:${VER}"
echo "  openops-frontend:latest"
