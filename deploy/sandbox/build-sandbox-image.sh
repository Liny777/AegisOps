#!/usr/bin/env bash
# 构建沙箱基础镜像并打成可 docker load 的离线工件（deploy/artifacts/）。
#
# 沙箱镜像变更频率远低于代码包，故独立成脚本（不进 build-artifacts.sh 每次全量流程）。
# 用法：
#   bash deploy/sandbox/build-sandbox-image.sh [版本标签，默认 v1]
# 后端机侧：
#   docker load -i openops-sandbox-image.tar.gz
#   然后管理台把 container_image 改成 openops-sandbox:<版本> 并填写变更原因（走审计）。
set -euo pipefail

VER="${1:-v1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_ROOT="$(cd "$HERE/../.." && pwd)/deploy/artifacts"
mkdir -p "$OUT_ROOT"

echo "[sandbox] 构建 openops-sandbox:${VER} …"
docker build --platform linux/amd64 -t "openops-sandbox:${VER}" -t openops-sandbox:latest "$HERE"

echo "[sandbox] 导出离线工件（gzip，docker load 原生认 .tar.gz）…"
docker save "openops-sandbox:${VER}" openops-sandbox:latest | gzip > "$OUT_ROOT/openops-sandbox-image.tar.gz"

echo "[sandbox] 完成：$OUT_ROOT/openops-sandbox-image.tar.gz"
ls -lh "$OUT_ROOT/openops-sandbox-image.tar.gz"
