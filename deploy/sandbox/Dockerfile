# OpenOps 沙箱基础镜像：python:3.11-slim + 一组克制的 SRE 排查工具 + 一组常用 pip 依赖。
#
# 运行时由平台强制施加安全基线（User=1000:1000 / 只读 rootfs / CapDrop=ALL /
# no-new-privileges / PidsLimit / 网络档位），本镜像不设 USER，保持瘦。
# 工具面刻意收在「连通性探测 + JSON 处理 + 进程观察 + 内容搜索」一级——不装 vim/net-tools/ssh 客户端。
# ripgrep 供 agentscope 官方 Grep 工具（绑容器后端）在容器内搜文件内容，见 runtime/sandbox_tool_backend.py。
# pip 依赖预装 requirements.txt 中一组常用库（运行态只读 rootfs 装不了包，故构建期烘焙）；
# 内网源同时持久化进镜像（/etc/pip.conf + UV_INDEX_URL），运行期 skill 自取额外包也自动走公司源。
#
# 构建/预置见 deploy/sandbox/build-sandbox-image.sh；管理台「沙箱与容量」把
# container_image 指向本镜像 tag（如 openops-sandbox:v1），默认仍是 python:3.11-slim。
FROM python:3.11-slim

# 内网同样打不到 deb.debian.org（且公司代理常不可达）：传 APT_MIRROR 换成公司 Debian 源。
# 传的是替换 http://deb.debian.org 的【基址】，如
#   --build-arg APT_MIRROR=https://mirrors.tools.huawei.com
# 其下须有 /debian 与 /debian-security。外网构建不传，走官方源。
# 基础镜像为 Debian 13(trixie)，apt 源是 deb822 格式的 /etc/apt/sources.list.d/debian.sources
# （无传统 /etc/apt/sources.list），故用 sed 改其 URIs。
ARG APT_MIRROR=
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
      unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      curl \
      jq \
      procps \
      iputils-ping \
      dnsutils \
      ca-certificates \
      less \
      ripgrep; \
    rm -rf /var/lib/apt/lists/*

# 内网构建走公司 PyPI 源（无公网直连）：由 build-sandbox-image.sh 透传，或直接
#   --build-arg PIP_INDEX_URL=https://mirrors.tools.huawei.com/pypi/simple \
#   --build-arg PIP_TRUSTED_HOST=mirrors.tools.huawei.com
# 外网构建不传，走默认 PyPI。与 sidecar 的 NPM_REGISTRY 惯例一致（deploy/sidecar/Dockerfile）。
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
# 把源【持久化进镜像】，让构建期 + 运行期（skill 在容器内自己 pip/uv install 时）都自动走公司源、
# 无需脚本带 -i：pip 读系统级 /etc/pip.conf；uv 不读 pip.conf，只认 UV_INDEX_URL 环境变量
# （镜像 ENV 在平台 Env:[] 下仍存活，已实测）。空值（外网构建未传源）时不写 pip.conf、
# UV_INDEX_URL 为空串 → pip 与 uv 都回落默认 PyPI（已实测，空串不报错）。
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      printf '[global]\nindex-url = %s\n' "$PIP_INDEX_URL" > /etc/pip.conf; \
      if [ -n "$PIP_TRUSTED_HOST" ]; then \
        printf 'trusted-host = %s\n' "$PIP_TRUSTED_HOST" >> /etc/pip.conf; \
      fi; \
    fi
ENV UV_INDEX_URL=${PIP_INDEX_URL}
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt
