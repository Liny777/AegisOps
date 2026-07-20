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

# 内网构建时用空值覆盖 docker 的代理预定义 ARG。
# 背景：构建机 ~/.docker/config.json 的 proxies 段会被 CLI 逐层注入 RUN；内网那个代理常不可达
# （实测 proxyuk.huawei.com:8080 超时），导致 apt/pip 明明配了内网源却仍走代理而失败。
# CLI 的 ParseProxyConfig 只判 key 在不在、不判值，所以显式传空值即可阻止 config.json 写入。
# 大小写都要传：只传小写的话，CLI 走到大写 key 发现不存在，照样从 config.json 注入大写版。
# 触发条件是「任一内网源变量非空」——只 export APT_MIRROR 的构建同样在内网；
# 绝不能无条件：公司网内走代理打公网 PyPI 是合法的第三种模式，清掉代理会掐断它。
NOPROXY_ARGS=()
if [ -n "${APT_MIRROR:-}" ] || [ -n "${PIP_INDEX_URL:-}" ]; then
  for v in http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; do
    NOPROXY_ARGS+=(--build-arg "$v=")
  done
  echo "[sandbox] 检测到内网源，已清空构建期代理变量（${#NOPROXY_ARGS[@]} 个 build-arg）"
fi

# APT_MIRROR / PIP_INDEX_URL / PIP_TRUSTED_HOST 有值才透传成 build-arg（空则不传，走公网默认源）。
docker build --platform linux/amd64 \
  ${APT_MIRROR:+--build-arg APT_MIRROR="$APT_MIRROR"} \
  ${PIP_INDEX_URL:+--build-arg PIP_INDEX_URL="$PIP_INDEX_URL"} \
  ${PIP_TRUSTED_HOST:+--build-arg PIP_TRUSTED_HOST="$PIP_TRUSTED_HOST"} \
  ${NOPROXY_ARGS[@]+"${NOPROXY_ARGS[@]}"} \
  -t "openops-sandbox:${VER}" -t openops-sandbox:latest "$HERE"

echo "[sandbox] 导出离线工件（gzip，docker load 原生认 .tar.gz）…"
docker save "openops-sandbox:${VER}" openops-sandbox:latest | gzip > "$OUT_ROOT/openops-sandbox-image.tar.gz"

echo "[sandbox] 完成：$OUT_ROOT/openops-sandbox-image.tar.gz"
ls -lh "$OUT_ROOT/openops-sandbox-image.tar.gz"
