#!/usr/bin/env bash
# Mac 侧一键打包内网部署工件（deploy/artifacts/）：
#   ① openops-frontend-image.tar.gz  前端一体镜像 nginx+dist（linux/amd64；测试/生产共用，
#                                    后端机地址经 env 注入 nginx 模板，不烘焙进镜像）
#   ② openops-sidecar-image.tar.gz   sidecar 镜像（linux/amd64）
#   镜像均 gzip（docker load 原生认 .tar.gz）——内网单文件上传限 500MB，sidecar 裸 tar 998M 超限
#   ③ openops-backend-src.tar.gz     后端代码包（内网 venv + pip 装依赖）
# 用法：bash deploy/build-artifacts.sh [版本标签，默认 git short sha]
set -euo pipefail
cd "$(dirname "$0")/.."
VER="${1:-$(git rev-parse --short HEAD)}"
OUT="deploy/artifacts"
rm -rf "$OUT" && mkdir -p "$OUT"   # 清旧工件——陈旧命名/中途失败残留不得混进 SHA256SUMS

echo "== ① 前端一体镜像（nginx+dist；构建强制 real——.env.local 的 mock 会被 vite build 读到，进程 env 压制）=="
( cd frontend && VITE_OPENOPS_API_MODE=real VITE_OPENOPS_TRANSPORT=agui npm run build )
# 防 mock 烘焙断言：API_MODE=real 时 mock facade 会被常量折叠+树摇整体移除——
# dist 里出现 mock 指纹字符串 = 烘焙错了（.env.local 的 mock 污染）
if grep -rq "mock 演示" frontend/dist/assets 2>/dev/null; then
  echo "   ✗ dist 含 mock 指纹——API_MODE 烘焙成 mock！检查构建 env"; exit 1
fi
echo "   API_MODE 烘焙=real ✓（mock facade 已被树摇移除）"
# dist staging 进构建上下文（deploy/frontend/dist 已 gitignore），打完即清
rm -rf deploy/frontend/dist && cp -R frontend/dist deploy/frontend/dist
docker build --platform linux/amd64 -f deploy/frontend/Dockerfile \
  -t "openops-frontend:${VER}" -t openops-frontend:latest deploy/frontend
rm -rf deploy/frontend/dist
docker save "openops-frontend:${VER}" openops-frontend:latest | gzip > "$OUT/openops-frontend-image.tar.gz"
echo "   → $OUT/openops-frontend-image.tar.gz"

echo "== ② sidecar 镜像（linux/amd64）=="
# 用 docker 引擎默认 builder（容器驱动的自建 buildx builder 可能无网导致 DeadlineExceeded）
docker build --platform linux/amd64 -f deploy/sidecar/Dockerfile \
  -t "openops-sidecar:${VER}" -t openops-sidecar:latest frontend/
docker save "openops-sidecar:${VER}" openops-sidecar:latest | gzip > "$OUT/openops-sidecar-image.tar.gz"
echo "   → $OUT/openops-sidecar-image.tar.gz"

echo "== ③ 后端代码包 =="
tar -czf "$OUT/openops-backend-src.tar.gz" \
  --exclude backend/.venv --exclude '__pycache__' --exclude '.pytest_cache' \
  backend/src backend/sql backend/tests backend/docs \
  backend/run.py backend/pyproject.toml backend/run-backend.sh backend/config \
  scripts/release_check.sh docs/release-checklist.md docs/deploy-intranet.md
echo "   → $OUT/openops-backend-src.tar.gz"

echo "== ④ 部署配套（compose/env 模板/systemd 打一个小包）=="
tar -czf "$OUT/openops-deploy-conf.tar.gz" deploy/systemd \
  deploy/frontend/docker-compose.yml \
  deploy/frontend/frontend.test.env.example deploy/frontend/frontend.prod.env.example \
  deploy/sidecar/sidecar.test.env.example deploy/sidecar/sidecar.prod.env.example
echo "   → $OUT/openops-deploy-conf.tar.gz"

( cd "$OUT" && shasum -a 256 openops-*.tar* > SHA256SUMS && cat SHA256SUMS )
echo "== 完成：版本 ${VER}，工件在 $OUT/ =="
