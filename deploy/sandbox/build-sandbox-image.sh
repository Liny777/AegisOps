#!/usr/bin/env bash
# 构建沙箱基础镜像并打成可 docker load 的离线工件（deploy/artifacts/）。
#
# 沙箱镜像变更频率远低于代码包，故独立成脚本（不进 build-artifacts.sh 每次全量流程）。
# 用法：
#   bash deploy/sandbox/build-sandbox-image.sh [版本标签，默认 v1]
# 内网构建（无公网直连，apt/pip 都走公司源）：先 export 三个源变量再跑本脚本——
#   export APT_MIRROR=http://mirrors.tools.huawei.com           # 替换 http://deb.debian.org 的基址
#   export PIP_INDEX_URL=https://mirrors.tools.huawei.com/pypi/simple
#   export PIP_TRUSTED_HOST=mirrors.tools.huawei.com
#   bash deploy/sandbox/build-sandbox-image.sh v1
# APT_MIRROR 用 http（内网 CA 签的证书基础镜像验不过，apt 完整性靠 GPG 签名）；
# pip 用 https + trusted-host（跳过该主机证书校验）。
# 外网构建：不 export，apt 走 deb.debian.org、pip 走默认 PyPI。
# 后端机侧：
#   docker load -i openops-sandbox-image.tar.gz
#   然后管理台把 container_image 改成 openops-sandbox:<版本> 并填写变更原因（走审计）。
set -euo pipefail

VER="${1:-v1}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_ROOT="$(cd "$HERE/../.." && pwd)/deploy/artifacts"
mkdir -p "$OUT_ROOT"

echo "[sandbox] 构建 openops-sandbox:${VER} …"
# APT_MIRROR / PIP_INDEX_URL / PIP_TRUSTED_HOST 有值才透传成 build-arg（空则不传，走公网默认源）。
docker build --platform linux/amd64 \
  ${APT_MIRROR:+--build-arg APT_MIRROR="$APT_MIRROR"} \
  ${PIP_INDEX_URL:+--build-arg PIP_INDEX_URL="$PIP_INDEX_URL"} \
  ${PIP_TRUSTED_HOST:+--build-arg PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST"} \
  -t "openops-sandbox:${VER}" -t openops-sandbox:latest "$HERE"

echo "[sandbox] 导出离线工件（gzip，docker load 原生认 .tar.gz）…"
docker save "openops-sandbox:${VER}" openops-sandbox:latest | gzip > "$OUT_ROOT/openops-sandbox-image.tar.gz"

echo "[sandbox] 完成：$OUT_ROOT/openops-sandbox-image.tar.gz"
ls -lh "$OUT_ROOT/openops-sandbox-image.tar.gz"
